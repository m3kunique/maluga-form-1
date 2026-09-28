import threading
import time
from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = "Run both queue worker and Telegram admin bot in concurrent threads"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting combined Worker and Telegram Bot service..."))

        worker_thread = threading.Thread(
            target=call_command,
            args=('run_worker',),
            daemon=True,
            name='QueueWorkerThread'
        )

        bot_thread = threading.Thread(
            target=call_command,
            args=('run_bot',),
            daemon=True,
            name='TelegramBotThread'
        )

        worker_thread.start()
        bot_thread.start()

        try:
            while worker_thread.is_alive() and bot_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write(self.style.NOTICE("Stopping all services..."))
