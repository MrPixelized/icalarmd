import asyncio
import aiofiles
import os
import subprocess
from datetime import datetime
from datetime import timedelta

from inotify import adapters
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

    def __call__(self):
        self.notify()


"""AlarmFile; class that is responsible for watching a single
ical file and triggering callbacks for events like alarms."""
class AlarmFile:
    def __init__(self, path):
        # Set up a file watcher for this ICS file
        self.path = path
        self.notifier = adapters.Inotify()
        self.notifier.add_watch(self.path)

        # Keep track of events/alarms to be triggered for this file
        self.alarms = []

    async def setup_alarms(self):
        self.clear_alarms()

        async with aiofiles.open(self.path, mode='r') as f:
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

                notification = Notification(title, details)

                # If the alarm trigger is a timedelta prior to start,
                # convert it to an absolute time
                if isinstance(alarm.trigger, timedelta):
                    trigger = event.begin + alarm.trigger
                else:
                    trigger = alarm.trigger

                trigger = datetime.utcfromtimestamp(trigger.timestamp)
                trigger = datetime.now() + timedelta(seconds=4)
                trigger = Trigger(trigger, notification)

                await trigger()

                print('alarm setup')

                # Set the notification to trigger at the right time
                self.alarms.append(trigger)

    def clear_alarms(self):
        for alarm in self.alarms:
            alarm.cancel()
            del alarm

    async def listen(self):
        # Set up alarms on start of listen
        await self.setup_alarms()

        # Loop through changes in the ICS file
        for event in self.notifier.event_gen(yield_nones=False):
            _, type_names, _, _ = event
            print(type_names)

            # if 'IN_CLOSE' in type_names:
            #     self.clear_alarms()
            #     return

            await self.setup_alarms()



async def main():
    path = os.path.abspath('./test.ics')
    f = AlarmFile(path)

    await f.listen()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
