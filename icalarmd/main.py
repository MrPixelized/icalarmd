import asyncio
import aiofiles
import os
import subprocess
import heapq
from datetime import datetime, timedelta
from pathlib import Path

from asyncinotify import Inotify, Mask
from typing import Generator
from ics import Calendar


"""Asynchronously triggers a function at or after a given time, cancelable"""
class Trigger:
    def __init__(self, timestamp: datetime, callback: callable):
        self.callback = callback
        self.timestamp = timestamp

        self.task = asyncio.create_task(self._trigger())

    def __call__(self):
        asyncio.ensure_future(self.task)

    def cancel(self):
        self.task.cancel()

    async def _trigger(self):
        """Waits asynchronously until self.timestamp to run self.callback"""
        dt =  self.timestamp - datetime.now()
        dt = dt.seconds

        if dt > 0:
            await asyncio.sleep(dt)

        self.callback()


"""Represents a notification that can be displayed to the system"""
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

    def __lt__(self, other):
        return True


"""Watches a directory for changes in ical files and automatically triggers
notifications based on the alarm settings in the files"""
class IcalAlarmWatcher:
    def __init__(self, path: Path):
        self.path = path
        # Store the inotify mask which is used throughout the watcher
        self.mask = Mask.MOVED_FROM  | \
                    Mask.MOVED_TO    | \
                    Mask.CREATE      | \
                    Mask.DELETE_SELF | \
                    Mask.IGNORED     | \
                    Mask.CLOSE_WRITE
        # Setup an inotify watching instance
        self.notifier = Inotify()

        # Queue of the various notifications to be sent at timestamps
        self.alarm_queue = []
        self.trigger = None

    async def init_alarms(self, path: Path):
        """Add the alarms for all of the ics files under a directory
        (recursively) to the alarm queue"""
        for ics in path.glob('**/*.ics'):
            await self.insert_alarm(ics)

    async def add_listeners(self, path: Path):
        """Add the required listeners for a directory and its children"""
        self.notifier.add_watch(path, self.mask)

        for child in self.get_subdirs(path):
            self.notifier.add_watch(child, self.mask)

    async def listen(self):
        """Listen for changes in files, making sure to track newly created
        files and update the ICS alarms as changes come in"""
        # Add listeners for the root directory and all of its children
        await self.init_alarms(self.path)
        await self.add_listeners(self.path)

        # Prime the first alarm to trigger a notification
        self.prime()

        # Loop through the inotify listeners to check for changes to files
        async for event in self.notifier:
            # Listen for new dirs, and add listeners for them
            if Mask.CREATE in event.mask and event.path.is_dir():
                await self.init_alarms(event.path)
                await self.add_listeners(event.path)
                self.prime()
            
            # A file changed, so regenerate the alarm queue and trigger
            # the new 'next alarm'
            elif not event.path.is_dir():
                await self.insert_alarm(event.path)
                self.prime()

    async def insert_alarm(self, path: Path):
        """Insert the alarms given by the file at the specified path
        into the alarm queue as a Notification object"""
        # Remove old alarms related to this file from the queue
        self.alarm_queue = list(
                filter(lambda x: x[-1] != path, self.alarm_queue))

        # Read content from the file
        try:
            async with aiofiles.open(path, mode='r') as f:
                content = await f.read()
        except FileNotFoundError:
            return

        # Parse the content into a calendar
        try:
            c = Calendar(content)
        except NotImplementedError:
            # TODO: Make sure this handles multiple calendars in a single file
            return

        # Loop trough the alarms in the calendar
        for event in c.events:
            for alarm in event.alarms:
                title = event.name
                details = event.description

                if not title:
                    title = "iCalendar alarm"
                if not details:
                    details = "Event happening soon"

                # If the alarm trigger is a timedelta prior to start,
                # convert it to an absolute time
                if isinstance(alarm.trigger, timedelta):
                    alarm_time = event.begin + alarm.trigger
                else:
                    alarm_time = alarm.trigger

                # Append this alarm to the queue
                alarm_time = datetime.fromtimestamp(alarm_time.timestamp)
                notification = Notification(title, details)
                heapq.heappush(self.alarm_queue, (alarm_time, notification, path))

    def prime(self):
        """Clear any alarm triggers already in memory, and use the most recent
        alarm in the alarm_queue to setup a new notification event"""
        # Cancel the current alarm so the new one can be triggered on its own
        if self.trigger is not None:
            self.trigger.cancel()

        # Go through the alarm queue and remove notifications that should
        # have been triggered some time ago
        while self.alarm_queue and self.alarm_queue[0][0] < datetime.now():
            heapq.heappop(self.alarm_queue)

        if not self.alarm_queue:
            return

        # Obtain the data of the next alarm to be triggered
        (timestamp, notification, path) = self.alarm_queue[-1]

        # Small function that triggers the notification for an alarm,
        # and then clears it from the queue
        def _notify_and_pop():
            notification.notify()
            heapq.heappop(self.alarm_queue)

        self.trigger = Trigger(timestamp, _notify_and_pop)
        self.trigger()

    @classmethod
    def get_subdirs(cls, path: Path) -> Generator[Path, None, None]:
        """Recursively list all directories under path"""
        for child in path.iterdir():
            if child.is_dir():
                yield child
                yield from cls.get_subdirs(child)


async def main():
    path = Path(os.path.abspath('/home/ischa/.local/share/caldav'))
    f = IcalAlarmWatcher(path)

    await f.listen()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
