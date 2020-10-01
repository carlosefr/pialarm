# -*- coding: utf-8 -*-
#
# Carlos Rodrigues <cefrodrigues@gmail.com>
#


import logging
import time
import threading
import queue

import pifacedigitalio as piface
import pifacecommon


# Limit this module's exports to the public bits...
__all__ = ["NUM_HARDWARE_PINS", "VIRTUAL_INPUT_PIN", "PiAlarm"]


log = logging.getLogger("pialarm")


# The number of physical digital inputs...
NUM_HARDWARE_PINS = 8

# Fake pin to trigger the alarm from software...
VIRTUAL_INPUT_PIN = NUM_HARDWARE_PINS

# Pre-defined timings for beeps, in seconds...
SHORT_BEEP_DURATION = 0.05
SHORT_BEEP_INTERVAL = 0.1
LONG_BEEP_DURATION = 0.15
LONG_BEEP_INTERVAL = 0.25

# Number of seconds to wait after each beep sequence to prevent
# back-to-back sequences from being (practically) indistiguishable...
BEEP_SEQUENCE_INTERVAL = 1.0

DEFAULT_BEEP_DURATION = SHORT_BEEP_DURATION
DEFAULT_BEEP_INTERVAL = SHORT_BEEP_INTERVAL

BEEP_SEQUENCES = {
    "init": {"times": 2},
    "timer": {"times": 1},
    "error": {"times": 5, "duration": SHORT_BEEP_DURATION * 0.75, "interval": SHORT_BEEP_INTERVAL * 0.75},
    "armed": {"times": 3, "duration": LONG_BEEP_DURATION, "interval": LONG_BEEP_INTERVAL},
    "accept": {"times": 2, "duration": LONG_BEEP_DURATION, "interval": SHORT_BEEP_INTERVAL},
    "alarm": {"times": 1, "duration": LONG_BEEP_DURATION * 2, "interval": SHORT_BEEP_INTERVAL},
}


# The resolution (in seconds) for responding to input violations with an
# alarm condition. Input violations lasting for less than this will not
# trigger (or extend) an alarm, which is also useful to supress glitches.
ALARM_CHECK_INTERVAL = 0.15

SOUNDER_TEST_DURATION = 2.0
STROBE_TEST_DURATION = 3.0
BUZZER_TEST_DURATION = 2.0


class PiAlarm:
    """
    Threaded alarm controller for off-the-shelf home sensors and sirens based on the PiFace Digital board.

    All outputs are assumed to (and must) be normally-closed, active-low, devices. This means outputs will
    be left floating (open, unconnected) most of the time, and will close (connect) to ground when active.

    All inputs are assumed to be normally-open by default, meaning they'll be interpreted as active when a
    connection to ground is detected (active-low), but any of them can be set to normally-closed making an
    input be interpreted as active when a connection to ground *isn't* detected.

    Pin numbers range from 0 to (NUM_HARDWARE_PINS - 1) independently for both inputs and outputs. There is
    an aditional VIRTUAL_INPUT_PIN number used internally to track software-induced alarms.

    Hardware Caveats:

    Each PiFace input has a resistor connected to +5V (pull-up) that ensures a logical-high state is always
    seen when not connected to ground (otherwise the state would change randomly), and input voltages must
    always be kept between 0 and +5V to avoid damage.

    Off-the-shelf alarm sensors are usually powered from +12V ou +24V supplies, so it isn't guaranteed that
    their pins never output more than +5V (although they often don't). Please check them with a voltmeter
    first or, better yet, apply additional isolation circuitry (e.g. an optocoupler and some resistors).

    Isolation circutry on both inputs and outputs is also important if long cables are used and/or devices
    are exposed to outside enviroments and interference, as it's usually the case in real-world usage.
    """

    def __init__(self, arm_input=None, armed_output=None, active_output=None,
                       buzzer_output=None, sounder_output=None, strobe_output=None,
                       arm_delay=30, alarm_delay=30, alarm_duration=900,
                       normally_closed_inputs=None, ignored_inputs=None):
        """
        Initialize the alarm's internal state and hardware (PiFace Digital).

        The `arm_input` pin allows an external device to arm/disarm the alarm. It's normally-open by default and,
        like all other inputs, can be set to normally-closed by including it in the `normally_closed_inputs` list.
        In this latter case, care must be taken to ensure the external device initializes before the alarm does,
        or the alarm may be inadvertently triggered.

        The `armed_output` and `active_output` pins can be used to (optionally) notify external devices that the
        alarm has been (respectively armed) or is currently sounding. The `armed_output` pin becomes low at the
        start of the arming grace period and remains so until the alarm is disarmed. The `active_output` pin is
        guaranteed to never be low unless the `armed_output` pin is also low.

        The `buzzer_output`, `sounder_output`, and `strobe_output` pins control the panel buzzer (for user feedback),
        external siren, and strobe light, respectively. These outputs are all optional and assumed to be active-low.

        The `arm_delay` parameter sets the amount of seconds to wait before unsealed (violated) inputs trigger
        the alarm once it has been armed. This should give the user enough time to leave the premises safely.

        The `alarm_delay` parameter sets the amount of seconds to wait before activating the alarm once one or more
        inputs become unsealed (violated) while armed. This should give the user enough time to disarm the alarm.

        Once triggered, the alarm will sound for `alarm_duration` seconds (unless it's disarmed), after which it will
        rearm itself automatically. The `alarm_delay` will be honored even if there are unsealed (violated) inputs at
        the moment `alarm_duration` expires.

        The `normally_closed_inputs` parameter can (optionally) be set to a list of input pin numbers that should be
        taken as unsealed (violated) when they are in a logical-high state instead of the default logical-low state.

        The `ignored_inputs` parameter can (optionally) be set to a list of input pin numbers whose state can never
        trigger the alarm. This list can also be set when the alarm is armed, reverting to whatever is set here when
        the alarm is disarmed (thus this parameter can be seen as the default list of ignored inputs).
        """

        log.debug("Alarm initialization starting...")

        self._state_lock = threading.Lock()
        self._armed = False
        self._sounding = False

        self._arm_input = arm_input
        self._armed_output = armed_output
        self._active_output = active_output
        self._buzzer_output = buzzer_output
        self._sounder_output = sounder_output
        self._strobe_output = strobe_output

        self._unsealed_inputs = set()
        self._ignored_inputs = set(ignored_inputs or [])
        self._default_ignored_inputs = self._ignored_inputs  # ...fallback on disarm.
        self._normally_closed_inputs = set(normally_closed_inputs or [])

        self._arm_delay = arm_delay
        self._alarm_delay = alarm_delay
        self._alarm_duration = alarm_duration

        self._pf = piface.PiFaceDigital()

        # Use a single "PortEventListener" for all inputs, instead of an "InputEventListener" for each input...
        self._listener = pifacecommon.interrupts.PortEventListener(pifacecommon.mcp23s17.GPIOB, self._pf, daemon=True)

        # Inputs won't trigger an interrupt until they change, must poll their initial state...
        for pin in range(NUM_HARDWARE_PINS):
            self._listener.register(pin, piface.IODIR_BOTH, self._input_change_handler)

            if self._arm_input is not None and pin == self._arm_input:
                continue

            high_state = bool(self._pf.input_pins[pin].value)
            normally_closed = pin in self._normally_closed_inputs

            if not high_state and normally_closed or high_state and not normally_closed:
                self._unsealed_inputs.add(pin)

        if self._unsealed_inputs:
            unsealed_inputs_str = ", ".join(str(pin) for pin in sorted(self._unsealed_inputs))
            log.info("These inputs are already unsealed on initialization: %s", unsealed_inputs_str)

        self._listener.activate()

        # Feedback beeps must be asynchronous, or they'd mess timings...
        if self._buzzer_output is not None:
            self._buzzer_enabled = True
            self._buzzer_queue = queue.SimpleQueue()
            self._buzzer_thread = threading.Thread(target=type(self)._buzzer_daemon, args=[self], daemon=True)
            self._buzzer_thread.start()

        if self._arm_input is not None:
            high_state = bool(self._pf.input_pins[self._arm_input].value)
            normally_closed = self._arm_input in self._normally_closed_inputs

            if high_state ^ normally_closed:
                log.warning("AUTO-ARMING on initialization from arm input pin state...")
                self.arm()

        self.beep(**BEEP_SEQUENCES["init"])
        log.info("Alarm initialized.")


    def __enter__(self):
        return self


    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


    def _input_change_handler(self, event):
        """React to hardware input changes (interrupts)."""

        high_state = event.direction == piface.IODIR_ON
        normally_closed = event.pin_num in self._normally_closed_inputs

        logic_state_str = "closed" if high_state else "open"
        usual_state_str = "normally closed" if normally_closed else "normally open"

        if self._arm_input is not None and event.pin_num == self._arm_input:
            log.debug("Arm input pin: %s (%s)", logic_state_str, usual_state_str)

            if high_state ^ normally_closed:
                self.arm()
            else:
                self.disarm()

            return

        if event.pin_num in self._ignored_inputs:
            usual_state_str += ", ignored"

        if high_state ^ normally_closed:
            log.info("Input unsealed: PIN %i (%s, %s)", event.pin_num, logic_state_str, usual_state_str)
            update_unsealed = self._unsealed_inputs.add
        else:
            log.info("Input sealed: PIN %i (%s, %s)", event.pin_num, logic_state_str, usual_state_str)
            update_unsealed = self._unsealed_inputs.discard

        previously_unsealed = bool(self._unsealed_inputs)

        with self._state_lock:
            update_unsealed(event.pin_num)

        if previously_unsealed and not self._unsealed_inputs:
            log.info("All unsealed inputs have resealed.")


    def _alarm_armed_daemon(self):
        """
        Asynchronously control the arming/disarming sequence and react to input violations.

        Note: This thread is running *only* when the alarm is in its armed state.
        """

        log.debug("Alarm thread starting...")
        assert self._armed

        if self._armed_output is not None:
            self._pf.output_pins[self._armed_output].turn_on()

        self._arming_grace_period()
        self._violation_check_loop()

        self._set_alarm_state(enabled=False)
        self._ignored_inputs = self._default_ignored_inputs

        if self._armed_output is not None:
            self._pf.output_pins[self._armed_output].turn_off()

        log.info("Alarm is DISARMED.")


    def _buzzer_daemon(self):
        """
        Asynchronously output sequences of beeps using the panel buzzer.

        Note: This thread is always running during normal operation.
        """

        log.debug("Buzzer thread starting...")

        while self._buzzer_enabled:
            try:
                sequence = self._buzzer_queue.get(timeout=0.1)
                log.debug("Beep sequence: %s", sequence)
            except queue.Empty:  # ...a chance to check the loop condition.
                continue

            for remaining in reversed(range(sequence["times"])):
                self._pf.output_pins[self._buzzer_output].turn_on()
                time.sleep(sequence["duration"])
                self._pf.output_pins[self._buzzer_output].turn_off()

                if remaining > 0:
                    time.sleep(sequence["interval"])

            time.sleep(BEEP_SEQUENCE_INTERVAL)


    def _arming_grace_period(self):
        """Wait before the alarm is armed, perhaps aborting the arming sequence if disarmed meanwhile."""

        now_ts = time.time()
        grace_end_ts = now_ts + self._arm_delay
        last_beep_ts = -1

        log.info("Waiting %d seconds before arming...", self._arm_delay)

        while self._armed and now_ts <= grace_end_ts:
            time.sleep(ALARM_CHECK_INTERVAL)

            if last_beep_ts <= now_ts - 1:
                self.beep(**BEEP_SEQUENCES["timer"])
                last_beep_ts = now_ts

            now_ts = time.time()

        if self._armed:  # ...not disarmed before expiring.
            self.beep(**BEEP_SEQUENCES["armed"])
            log.info("Alarm is ARMED.")


    def _violation_check_loop(self):
        """React to input violations when armed, activating or deativating outputs when appropriate."""

        alarm_trigger_ts = -1
        alarm_reset_ts = -1
        last_beep_ts = -1

        while self._armed:
            time.sleep(ALARM_CHECK_INTERVAL)

            now_ts = time.time()

            if self._unsealed_inputs - self._ignored_inputs and alarm_trigger_ts <= 0:
                alarm_trigger_ts = now_ts + self._alarm_delay

                log.info("Alarm will trigger in %d seconds...", self._alarm_delay)
                continue

            if 0 < alarm_trigger_ts >= now_ts and last_beep_ts <= now_ts - 1:
                self.beep(**BEEP_SEQUENCES["timer"])
                last_beep_ts = now_ts

                log.debug("Waiting for possible disarm...")
                continue

            if 0 < alarm_trigger_ts <= now_ts and alarm_reset_ts <= 0:
                alarm_reset_ts = alarm_trigger_ts + self._alarm_duration
                self._set_alarm_state(enabled=True)

                log.warning("Disarm grace period expired. Alarm ACTIVE for %d seconds.", self._alarm_duration)
                continue

            if 0 < alarm_reset_ts <= now_ts:
                self._set_alarm_state(enabled=False)
                alarm_trigger_ts = -1
                alarm_reset_ts = -1
                last_beep_ts = -1

                self.beep(**BEEP_SEQUENCES["armed"])
                log.info("Alarm duration expired. Alarm inactive and REARMED.")
                continue

            if alarm_trigger_ts <= now_ts <= alarm_reset_ts and last_beep_ts <= now_ts - 1:
                self.beep(**BEEP_SEQUENCES["alarm"])
                last_beep_ts = now_ts


    def _set_alarm_state(self, enabled=True):
        """Activate or deactivate the alarm's main outputs (strobe and external siren)."""

        log.debug("Sounding alarm: %s", "TRUE" if enabled else "FALSE")

        for pin in (self._active_output, self._sounder_output, self._strobe_output):
            if pin is not None:
                self._pf.output_pins[pin].value = enabled

        self._sounding = enabled


    @property
    def armed(self):
        """Returns whether an unsealed (violated) input can trigger the alarm."""

        return self._armed


    @property
    def active(self):
        """Returns whether the alarm is currently sounding."""

        return self._armed and self._sounding


    @property
    def unsealed_inputs(self):
        """Returns a sorted list of currently unsealed (violated) inputs."""

        return list(sorted(self._unsealed_inputs))


    def arm(self, ignored_inputs=None):
        """
        Allow (pre-existing or future) input violations to trigger the alarm.

        The `ignored_inputs` parameter can (optionally) be set to a list of inputs
        that can never trigger the alarm. The set of ignored inputs will revert to
        whatever was set on initialization when the alarm is disarmed.
        """

        with self._state_lock:
            if self._armed:
                log.debug("The alarm is already armed, skipping.")
                return

            self.beep(**BEEP_SEQUENCES["accept"])

            if ignored_inputs is not None:
                self._ignored_inputs = ignored_inputs

            self._armed = True
            self._alarm_armed_thread = threading.Thread(target=type(self)._alarm_armed_daemon, args=[self], daemon=True)
            self._alarm_armed_thread.start()


    def disarm(self):
        """Stop the alarm (if active) and inhibit input violations from triggering it."""

        with self._state_lock:
            if not self._armed:
                log.debug("The alarm is already disarmed, skipping.")
                return

            self.beep(**BEEP_SEQUENCES["accept"])

            self._armed = False
            self._alarm_armed_thread.join()


    def close(self):
        """Stop the alarm (if active) and reset the hardware to its default state."""

        if self._armed:
            self.disarm()

        if self._buzzer_enabled:
            self._buzzer_enabled = False  # ...signal the thread to exit.
            self._buzzer_thread.join()

        self._pf.output_port.all_low()
        time.sleep(0.01)  # ...allow the outputs to settle on low.
        self._pf.deinit_board()  # ...disables interrupts as well.


    def sounder_test(self):
        """Activate the external sounder for a few seconds to confirm its operation."""

        if self._armed:
            logging.info("Alarm is armed, skipping sounder test.")
            return

        if self._sounder_output is None:
            logging.info("No sounder attached, skipping sounder test.")
            return

        self._pf.output_pins[self._sounder_output].turn_on()
        time.sleep(SOUNDER_TEST_DURATION)
        self._pf.output_pins[self._sounder_output].turn_off()


    def strobe_test(self):
        """Activate the strobe for a few seconds to confirm its operation."""

        if self._armed:
            logging.info("Alarm is armed, skipping strobe test.")
            return

        if self._strobe_output is None:
            logging.info("No strobe attached, skipping strobe test.")
            return

        self._pf.output_pins[self._strobe_output].turn_on()
        time.sleep(STROBE_TEST_DURATION)
        self._pf.output_pins[self._strobe_output].turn_off()


    def beep(self, times=1, duration=DEFAULT_BEEP_DURATION, interval=DEFAULT_BEEP_INTERVAL, queue=False):
        """
        Activate the panel buzzer one or more times in a row, for user feedback.

        By default, with `queue` set to false, this will take effect right after the current
        sequence of beeps (if any) completes, discarding any sequence that hasn't yet started.
        """

        if self._buzzer_output is None:
            return

        if not queue and not self._buzzer_queue.empty():  # ...clear the queue for immediate action.
            self._buzzer_queue = type(self._buzzer_queue)()

        self._buzzer_queue.put({"times": times, "duration": duration, "interval": interval})


    def set_virtual_input_state(self, closed=True):
        """Set the state of the virtual (software) input pin, triggering the alarm if armed."""

        normally_closed = VIRTUAL_INPUT_PIN in self._normally_closed_inputs

        logic_state_str = "closed" if closed else "open"
        usual_state_str = "normally closed" if normally_closed else "normally open"

        if closed != normally_closed:
            log.info("Input unsealed: VIRTUAL (%s, %s)", logic_state_str, usual_state_str)
            update_unsealed = self._unsealed_inputs.add
        else:
            log.info("Input sealed: VIRTUAL (%s, %s)", logic_state_str, usual_state_str)
            update_unsealed = self._unsealed_inputs.discard

        previously_unsealed = bool(self._unsealed_inputs)

        with self._state_lock:
            update_unsealed(VIRTUAL_INPUT_PIN)

        if previously_unsealed and not self._unsealed_inputs:
            log.info("All inputs back in sealed state.")


# EOF
