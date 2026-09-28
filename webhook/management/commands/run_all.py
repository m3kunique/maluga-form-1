import threading
import time
import signal
import sys
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Run both queue worker and Telegram admin bot in concurrent threads"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting combined Worker and Telegram Bot service..."))

        stop_event = threading.Event()

        def signal_handler(sig, frame):
            self.stdout.write(self.style.NOTICE("Received stop signal, shutting down gracefully..."))
            stop_event.set()

        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except ValueError:
            pass

        def worker_loop():
            while not stop_event.is_set():
                try:
                    call_command('run_worker')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Worker crashed: {e}. Restarting in 5s..."))
                    time.sleep(5)

        def bot_loop():
            while not stop_event.is_set():
                try:
                    call_command('run_bot')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Bot crashed: {e}. Restarting in 5s..."))
                    time.sleep(5)

        worker_thread = threading.Thread(
            target=worker_loop,
            daemon=True,
            name='QueueWorkerThread'
        )

        bot_thread = threading.Thread(
            target=bot_loop,
            daemon=True,
            name='TelegramBotThread'
        )

        worker_thread.start()
        bot_thread.start()

        while not stop_event.is_set():
            time.sleep(1)

        self.stdout.write(self.style.SUCCESS("Worker and Bot service stopped."))
