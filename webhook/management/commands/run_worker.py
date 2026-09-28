import time
import signal
import sys
from django.core.management.base import BaseCommand
from webhook.services.queue_worker import process_pending_submissions


class Command(BaseCommand):
    help = "Run background queue worker to write submissions to Google Sheets"

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=3, help='Polling interval in seconds')
        parser.add_argument('--batch-size', type=int, default=20, help='Max rows per batch')

    def handle(self, *args, **options):
        interval = options['interval']
        batch_size = options['batch_size']

        self.stdout.write(self.style.SUCCESS(
            f"Starting queue worker (interval={interval}s, batch_size={batch_size})..."
        ))

        running = True

        def signal_handler(sig, frame):
            nonlocal running
            self.stdout.write(self.style.NOTICE("Stopping worker gracefully..."))
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        consecutive_errors = 0
        while running:
            try:
                result = process_pending_submissions(batch_size=batch_size)
                processed = result['processed']
                remaining = result['remaining']
                failed = result['failed']

                if processed > 0 or failed > 0:
                    self.stdout.write(
                        f"Processed: {processed}, Failed: {failed}, Remaining in queue: {remaining}"
                    )
                    consecutive_errors = 0
                    # Short throttle between active batches to respect Google Sheets write quotas
                    time.sleep(1.0)
                else:
                    time.sleep(interval)

            except Exception as e:
                consecutive_errors += 1
                self.stdout.write(self.style.ERROR(f"Worker iteration error: {e}"))
                # Exponential backoff on consecutive uncaught errors
                backoff = min(60, interval * (2 ** min(consecutive_errors, 5)))
                time.sleep(backoff)
