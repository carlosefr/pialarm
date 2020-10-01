# Alarm controller based on the PiFace Digital board

The `PiAlarm` class implements a threaded alarm controller for off-the-shelf home security sensors and sirens based on the [PiFace Digital](http://www.piface.org.uk/products/piface_digital/) add-on board to the [Raspberry Pi](https://www.raspberrypi.org/). It has been tested with a discarded [Texecom Odyssey 5E](https://www.texe.com/uk/products/series/external-sounders/premier-series/) siren but should work with other equipment.

**Note:** This is code written for fun and learning purposes, so don't expect something production-ready.

All **outputs** are assumed to (and must) be normally-closed, active-low, devices. This means an output will be left floating (open, unconnected) most of the time, and will close (connect) to ground to activate the attached device.

All **inputs** are assumed to be normally-open by default, meaning they'll be interpreted as active when a connection to ground is detected (active-low). However, any of them can be set to normally-closed, meaning it will be interpreted as active when a connection to ground **isn't** detected.

Configurable **pin numbers** range from 0 to `NUM_HARDWARE_PINS - 1` independently for both inputs and outputs. There is an aditional `VIRTUAL_INPUT_PIN` number used internally to track software-induced alarms (e.g. computer vision).

Using some kind of **isolation circuitry** to protect the hardware is recommended, see the `circuits/isolation.png` diagram for an example.

Please refer to the `pialarm` module's docstrings for the **details and usage**.
