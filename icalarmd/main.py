import asyncio
import aiofiles
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from asyncinotify import Inotify, Mask
from typing import Generator
from ics import Calendar


"""Class that asynchronously triggers a function at a given time,
cancelable"""
class Trigger:
    def __init__(self, timestamp: datetime, callback: callable):
        self.callback = callback
        self.timestamp = timestamp

        self.task = asyncio.create_task(self._trigger())

    async def __call__(self):
        await self.task

    def cancel(self):
        self.task.cancel()

    async def _trigger(self):
        """Waits asynchronously until self.timestamp to run self.callback"""
        dt =  self.timestamp - datetime.now()
        dt = dt.seconds

        if dt <= 0:
            return

        await asyncio.sleep(dt)
        self.callback()


class Notification:
    def __init__(self, title: str, details: str):
        self.title = title
        self.details = details

    def notify(self):
        subprocess.run(['notify-send', self.title, self.details])

    def __str__(self):
        return f'''{self.title}; {self.details}'''

    def __call__(self):
        self.notify()


class IcalAlarmWatcher:
    def __init__(self, path: Path):
        self.path = path
        self.mask = Mask.MOVED_FROM  | \
                    Mask.MOVED_TO    | \
                    Mask.CREATE      | \
                    Mask.DELETE_SELF | \
                    Mask.IGNORED     | \
                    Mask.CLOSE_WRITE
        self.notifier = Inotify()

        # Add listeners for the root directory and all of its children
        self.add_listeners(self.path)

    def add_listeners(self, path: Path):
        """Add the required listeners for a directory and its children"""
        self.notifier.add_watch(path, self.mask)

        for child in self.get_subdirs(path):
            self.notifier.add_watch(child, self.mask)

    async def listen(self):
        """Listen for changes in files, making sure to track newly created
        files and update the ICS alarms as changes come in"""

        async for event in self.notifier:
            # Listen for new dirs, and add listeners for them
            if Mask.CREATE in event.mask and event.path.is_dir():
                self.add_listeners(event.path)
            
            # A file changed, so regenerate the alarm queue
            elif Mask.CLOSE_WRITE in event.mask and not event.path.is_dir():
                await self.insert_alarm(event.path)

    async def insert_alarm(self, path: Path):
        """Insert the alarms given by the file at the specified path
        into the alarm queue as a Notification object"""

        async with aiofiles.open(path, mode='r') as f:
            content = await f.read()

        c = Calendar(content)

        for event in c.events:
            for alarm in event.alarms:
                try:
                    desc = event.description
                    title, details = desc.split('\n', 1)
                except AttributeError:
                    title = "iCalendar alarm"
                    details = "Event happening soon"

                # If the alarm trigger is a timedelta prior to start,
                # convert it to an absolute time
                if isinstance(alarm.trigger, timedelta):
                    alarm_time = event.begin + alarm.trigger
                else:
                    alarm_time = alarm.trigger

                notification = Notification(title, details)

                # Append this alarm to the queue
                self.alarm_queue.append((alarm_time, notification))
                print("Setup alarm", notification)

    @classmethod
    def get_subdirs(cls, path: Path) -> Generator[Path, None, None]:
        """Recursively list all directories under path"""

        if not path.is_dir():
            return

        for child in path.iterdir():
            yield from cls.get_subdirs(child)


async def main():
    path = Path(os.path.abspath('./test'))
    f = IcalAlarmWatcher(path)

    await f.listen()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
