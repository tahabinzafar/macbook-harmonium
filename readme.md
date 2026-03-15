# Harmonium

A MacBook harmonium. Keyboard plays the notes, the laptop lid pumps the bellows.

Built with Python - pygame for rendering, sounddevice for real-time audio synthesis, and pybooklid to read the lid angle sensor via IOKit HID.

## Setup

```
pip3 install sounddevice numpy pygame pybooklid
python3 harmonium.py
```

Requires macOS. Python latest version works fine.

## How it works

**Audio:** The synth generates sound sample-by-sample in a real-time callback. Each note is built from layered waveforms (saw, square, sine) with an LFO tremolo to get that reedy harmonium sound. Envelope handles attack/release so notes don't click on and off.

**Bellows:** The lid angle sensor (Apple HID device `0x05AC:0x8104`) reports the hinge angle in degrees. The app tracks the *velocity* of lid movement and feeds it into a simulated air reservoir that decays over time. More pumping = more air pressure = louder sound. Stop moving the lid and it fades out naturally, same as a real harmonium.

**Fallback:** If the sensor isn't available (pre-2019 Macs, or iMacs for obvious reasons), mouse Y position controls volume instead.

## Controls

**A S D F G H J K L** - Sa Re Ga Ma Pa Dha Ni Sa' Re'
**Z X** - flats
**↑ ↓** - shift octave
**Space** - recalibrate sensor

## Lid sensor compatibility

Most MacBooks from 2019 onwards have the sensor. Check yours:

```
hidutil list --matching '{"VendorID":0x05AC,"ProductID":0x8104,"PrimaryUsagePage":32,"PrimaryUsage":138}'
```

If that returns a device, you're good.

## Stack

- **pygame** - window, key events, rendering at 60fps
- **sounddevice** - low-latency audio output via PortAudio
- **numpy** - audio buffer math
- **pybooklid** - lid angle sensor reads over IOKit HID

## Credits

Lid sensor reverse engineering by [Sam Henri Gold](https://github.com/samhenrigold/LidAngleSensor). Python wrapper by [tcsenpai/pybooklid](https://github.com/tcsenpai/pybooklid). The lid-as-bellows concept from [MacMonium](https://github.com/pranavgawaii/MacMonium) by Pranav Gawai.
