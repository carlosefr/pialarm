#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Carlos Rodrigues <cefrodrigues@gmail.com>
#


import sys
import os
import logging
import signal
import time

from argparse import ArgumentParser

from pialarm import PiAlarm


# Using the root logger directly is bad practice. All of our logging (including modules) should
# exist within the "myapp.*" namespace to avoid messing with logging inside third-party modules...
log = logging.getLogger("pialarm")


# Wait this number of seconds after arming before an input violation can
# trigger the alarm, to give the user enough time to exit the premises.
ARM_GRACE_PERIOD = 60

# Wait this number of seconds before an input violation triggers the alarm,
# to give the user time to disarm it after entering the premises. The alarm
# will always trigger if it's not disarmed before this period expires, even
# if all inputs have resealed...
ALARM_GRACE_PERIOD = 30

# Once the alarm is triggered, it will remain active for this number of
# seconds regardless of input state. Once this time elapses, the alarm
# will be reset and rearm itself automatically (maybe triggering again).
ALARM_DURATION = 900


ALARM_SETTINGS = {
    "normally_closed_inputs": [1],  # ...siren tamper switch, pressed when the cover is closed.
    "ignored_inputs": [2],  # ...just for testing purposes.
    "arm_input": 0,  # ...outermost button in the PiFace board.
    "armed_output": 0,  # ...outermost LED in the PiFace board.
    "active_output": 1,
    "sounder_output": 2,
    "strobe_output": 3,
    "buzzer_output": 4,
    "arm_delay": ARM_GRACE_PERIOD,
    "alarm_delay": ALARM_GRACE_PERIOD,
    "alarm_duration": ALARM_DURATION,
}


def parse_args():
    """Parse and enforce command-line arguments."""

    # Disable the automatic "-h/--help" argument to customize its message...
    parser = ArgumentParser(description="Raspberry Pi alarm system using PiFace Digital.", add_help=False)

    parser.add_argument("-h", "--help", action="help", help="Show the available options and exit.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Produce extra output for debugging purposes.")
    parser.add_argument("--log", action="store", metavar="filename", help="Send log messages into the specified file.")

    return parser.parse_args()


def main():
    args = parse_args()

    # Formatting and pointing all messages to standard error (or a file) at the root logger level
    # ensures everything gets a timestamp and doesn't get lost in some console that nobody reads...
    format = logging.Formatter("%(asctime)s: %(levelname)s: %(message)s")
    handler = logging.handlers.WatchedFileHandler(args.log) if args.log else logging.StreamHandler(sys.stderr)
    handler.setFormatter(format)
    logging.getLogger().addHandler(handler)

    # Leaving the root logger with the default level, and setting it in our own logging hierarchy
    # instead, prevents accidental triggering of third-party logging, just like $DEITY intended...
    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    def signal_handler(signum, frame):
        if "alarm" in locals():  # ...alarm initialized.
            alarm.close()

        os._exit(0)

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
        signal.signal(s, signal_handler)

    with PiAlarm(**ALARM_SETTINGS) as alarm:
        while True:
            # The alarm code does most of its work in separate threads, so we can do other stuff here...
            log.debug("Status: armed=%r; unsealed=%r; active=%r", alarm.armed, alarm.unsealed_inputs, alarm.active)

            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass


# EOF
