# Runbook - what to do next, step by step

Copy-paste instructions for the person with the watch. No prior context needed. Each task
says what it is for, exactly what to type, what a good result looks like, and what to send
back.

This file is the current task list and gets rewritten as tasks are done. `HANDOFF.md` is the
reasoning behind it; you do not need to read it to follow this.

**Where:** the **Linux Mint** side of the X230, in a terminal. Not Windows. Windows stays
useful only for making SuuntoLink captures.

**Golden rule:** every command below is safe except the ones in task 3, which are clearly
marked. Nothing in tasks 0 to 2 writes anything to the watch.

**Where we are: tasks 0 to 4 are done, and the project's goal is met.** On 2026-08-04 a route
built from a GPX file alone was written to the watch, and the watch shows it with its two
waypoints. Nothing between a GPX file and the watch screen is guesswork any more.

What is left needs the watch but is smaller: putting your own routes back, and closing two
protocol details in task 5 below.

Before any write from now on: `./tools/write_nav.py nav --save backup`. It makes the write
reversible.

---

## Task 0 - one-time setup (15 min, do this once)

### 0.1 Get the code

```
cd ~
git clone https://github.com/skinnie/sommet.git
cd sommet
```

If you have an SSH key set up on GitHub you can use
`git clone git@github.com:skinnie/sommet.git` instead. The HTTPS address above
needs nothing set up, so prefer it.

Later, to get the latest version, just:

```
cd ~/sommet
git pull
```

### 0.2 Get the data files

The repository deliberately contains no captures and no Suunto files. Ask Vincent for the
`assets/` folder and unpack it so that it sits **inside** `~/sommet`, like this:

```
~/sommet/assets/ambit3 pcap/route12km        <- and the 8 other captures
~/sommet/assets/descr+XXXXXXXX+2.4.17        <- the SuuntoLink schema file
```

Check it landed in the right place:

```
cd ~/sommet
ls assets/ambit3\ pcap/ | head
ls assets/descr+*
```

Both commands must print file names. If the second one prints
`ls: cannot access 'assets/descr+*'`, the schema file is missing and task 1 will refuse to
run - that is deliberate, it would otherwise give you a wrong answer.

### 0.3 Install what is needed

```
sudo apt update
sudo apt install -y python3 build-essential libhidapi-hidraw0 python3-hid
```

That is all, on Mint and on Ubuntu. `build-essential` provides the `make` used in 0.4, and
the other two are what lets Python talk to the watch over USB.

Check it took:

```
python3 -c "import hid; print('hid module OK')"
```

**Good result:** it prints `hid module OK`.

**If it prints `ModuleNotFoundError: No module named 'hid'`,** or if `apt` said it could not
find `python3-hid` because you are not on Mint or Ubuntu, go to 0.3a.

### 0.3a Fallback: a private Python environment for this project

Skip this if 0.3 worked.

**Why:** recent Linux distributions refuse to let `pip` install things next to the system's
own Python, so as to stop a stray package from breaking the operating system. Trying it
anyway gives an error mentioning `externally-managed-environment`. That is a fence, not a
fault. The way around it is a *virtual environment*: a private folder holding its own copy of
Python and its own packages, which cannot affect the rest of the machine.

Create one inside the repository and install into it:

```
cd ~/sommet
python3 -m venv venv
source venv/bin/activate
pip install hid
```

**Good result:** `pip` finishes without an error, and your prompt now begins with `(venv)`.
That prefix is how you know you are inside the environment.

**If `python3 -m venv venv` fails with a message about `ensurepip`,** the piece of Python
that creates environments is not installed. The error itself names the package to install,
and it is one of these two:

```
sudo apt install -y python3-venv
```

Then delete the half-made folder and try again:

```
rm -rf ~/sommet/venv
cd ~/sommet
python3 -m venv venv
source venv/bin/activate
pip install hid
```

**The one thing to remember about a venv:** it is not permanent. It applies to the terminal
window you activated it in, and nothing else. **Every time you open a new terminal**, before
running any command from this runbook:

```
cd ~/sommet
source venv/bin/activate
```

If you forget, `No module named 'hid'` comes back. That message means "you are outside the
environment", not "the install failed". Re-activate and carry on.

The `venv` folder is deliberately not tracked by git, so it will never get in the way of a
`git pull`.

### 0.3b If you see `module 'hid' has no attribute 'Device'`

That was a real bug on our side, fixed on 2026-08-04. Two different Python packages are both
imported as `hid` and their APIs differ; the tool used to require one of them and Mint ships
the other. It now accepts either. Update and the error goes away:

```
cd ~/sommet
git pull
```

If it persists after a `git pull`, send us the output of `python3 -c "import hid; print(hid.__file__)"`.

### 0.4 Check everything works

```
cd ~/sommet
make -C csrc
python3 tools/selftest.py
```

**Good result:** the last line says `20/20 checks pass`.

**If it says `captures not found`:** the `assets/` folder is not in the right place, go back
to 0.2.

**If some lines say `skip`:** that is fine, it means an optional piece is missing, and the
count will be lower than 20. Send the output and we will tell you if it matters.

### 0.5 Check the watch is reachable

Plug the watch in with its cable, then:

```
lsusb | grep 1493
```

**Good result:** one line containing `ID 1493:` and your watch. `1493` is Suunto's vendor
number.

**If nothing prints:** the cable, the port, or the watch is asleep. Try another USB port and
press a button on the watch.

Then the real test, which is the tool itself:

```
cd ~/sommet
./tools/write_nav.py settings --redact
```

Task 1 explains what the output means. Here, only the failures matter, and each one has a
different cause.

**If you get a `Traceback` with lines of Python in it,** your copy of the code is out of
date. Every failure the tool expects is reported as one readable sentence, never as a
traceback. Update and try again:

```
cd ~/sommet
git pull
./tools/write_nav.py settings --redact
```

**If it says `no Ambit3 on the USB bus`** while `lsusb` did show the watch: unusual, since
both look at the same place. Try another port, and send us both outputs.

**If it says `none of them openable`,** the watch is seen but your user is not allowed to
talk to it. That is the common one. The message prints the fix; this is the same thing with
a word of explanation.

Listing a USB device needs no permission, opening it does, and they are separate things -
which is why `lsusb` can succeed while the tool cannot. Look at the device nodes:

```
ls -l /dev/hidraw*
```

If they are owned by `root` with no permissions for anyone else, that is the whole problem.
Grant access:

```
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1493", MODE="0666"' | sudo tee /etc/udev/rules.d/99-suunto.rules
sudo udevadm control --reload-rules
```

**Unplug the watch and plug it back in** - the rule only applies to devices that appear after
it is loaded - then run the command again.

Do not assume openambit already took care of this. A rule written for openambit's own way of
reaching the watch covers a different device node than the one used here, so openambit can
work perfectly while this tool cannot.

---

## Task 1 - the `IsNspCapable` question: DONE, 2026-08-04

Nothing to do here. Kept for the record, since it explains what task 3 is for.

**The question was:** the watch stores a Bluetooth key for the phone, `EncodingKey`, and next
to it a flag `IsNspCapable`. An old capture had that flag at `1`; a fresh pairing gave `0`. Did
the flag simply record that the pairing had been made from the phone's Bluetooth settings
rather than from inside an app?

**The answer is no.** You paired from inside the Suunto app and it still read `0`. Two things
you found along the way made it more useful than a plain no:

- an **unpaired** watch does not appear in iOS Settings > Bluetooth at all, so pairing cannot
  be started from there and step 1.2 was impossible as written. Once the Suunto app has paired
  it, it does show up and can be forgotten from Settings. That asymmetry is a finding about
  iOS, not a mistake on your side, and it decides who owns the pairing flow in our future app;
- the flag is `0` on all eight slots, empty ones included, where the old capture had `1` on all
  eight.

So the flag is not something pairing sets, and we now know we have to write it ourselves. That
is part of a later task, and it needs the writing path proven first - which is task 3.

Thank you for the `--redact` output, it was exactly what was needed and it was safe to send as
is.

## Task 2 - back up what is on the watch: DONE, 2026-08-04

**Why:** task 3 erases every route and every waypoint on the watch. There is no undo, and no
way to read them back yet. So they get written down on paper first.

On the watch, go through the navigation menus and note, for each route: its **name**, and
roughly where it goes. For each POI or waypoint: its **name** and its **coordinates**, which
the watch can display.

If that is a lot of entries, photograph the screens instead. The point is only to be able to
put them back later.

You can also see what the old capture thought was on the watch, which may jog your memory:

```
cd ~/sommet
./tools/decode_route.py "assets/ambit3 pcap/ambit3full" | head -40
```

Tell us when this is done, and confirm in writing that you accept losing what is on the
watch. Then, and only then, task 3.

---

## Task 3 - the first real write: DONE, 2026-08-04

It worked. The routes disappeared from the watch, and the watch was still answering
afterwards. Kept short, for the record:

- the rehearsal matched the capture, `OK 4 0x0b16/0x0b18 payloads`;
- the watch's own memory map matched every address and size the project had assumed - the
  first time that was confirmed live rather than from a capture;
- 7 messages, 206 bytes, no error, and `settings` still answered afterwards.

**Your question, "not sure if it is good to delete gpsSGEE": nothing touched it.** Those three
`OK Waypoints / Routes / GpsSGEE` lines are a *check*, not a write: the tool asks the watch
where its regions are and compares the answer to what it expected. Your own output proves it.
The two writes were 14 and 40 bytes of payload, which is 8 bytes of header plus 6 and 32 bytes
of body - the two navigation region headers, at `0x005000` and `0x14c080`. `GpsSGEE` lives at
`0x0704e0` and was never addressed. Your AGPS data is intact.

**Your POIs, though, are our fault.** The documentation said omitting one message would leave
them alone. It said the opposite of the truth, and your run is what proved it: a navigation
write wipes the POI store, and that last message is what puts it back. Reading the capture
again with that in mind shows SuuntoLink asking for the POI list, wiping, then writing the
list back. The tool now does the same, and its version of that message reproduces the
capture's byte for byte, so this will not happen to anyone again.

That does not give you yours back, and you answered the question that decides how. Of the
three ways the watch accepts a POI - current position, typed coordinates, or the Suunto app
with "use on the watch" then a cable sync - **use the third one**. It is the only exact one:
no typing, and full precision. The watch's own entry screen takes five decimals where the
record stores seven, so anything typed by hand lands within about a metre and is not the same
POI you had.

**Do that before task 4, not after.** Task 4 asks whether the POIs survive a route write,
which is the fix from this task being tested. With an empty POI list that question has no
answer. Restore them first and the check becomes real.

We have not built a command to write POIs ourselves. It is a genuine gap, since the exact
route depends on the Suunto app and SuuntoLink and this project exists to do without them, but
it is not needed to finish task 4 and the format is fully known, so it is a short job when it
comes up rather than now.

---

## Task 4 - write a real route: DONE, 2026-08-04

It worked. `Gare du Nord` appeared on the watch with its two waypoints, and the POI survived.

That is the whole point of the project reached: a GPX file, simplified the way SuuntoLink
simplifies it, turned into the watch's own binary format, framed, sent over USB, and displayed.
336 points out of the file's 1066, four chunks of point data, both checksums, both index tables.

Thank you - none of the last two days could have happened without the watch.

---

## Task 5 - put your routes back, and two small unknowns (30 min)

### 5.1 Your Grand Tour, back

Task 4 replaced the whole database, so your own routes went. The backup you took brings them
back, headers and all:

```
cd ~/sommet
./tools/write_nav.py nav --save after-task4      # keep the route-write result first
./tools/write_nav.py restore before-task4        # rehearsal
./tools/write_nav.py restore before-task4 --write
```

**Send us `after-task4-routes.bin` before you do that.** It is the first chance to compare what
the watch actually stored against what we meant to send, byte for byte. The watch displaying the
route is good; that file would be proof.

### 5.2 The last two protocol unknowns

Both are small, both need the watch, and both close a line in the handoff's unknowns table.
Neither is dangerous, but both are writes, so take a backup first.

**Does the watch check the closing hash?** Every write ends with a SHA-256 of the region. We
compute it correctly and have never found out whether the watch cares. If it does not, a whole
class of mistake goes unnoticed - which is worth knowing before anyone builds an app on this.

**What is the 4-byte word at offset 4 of the `0x0b18`?** It is the only field of the protocol we
have never identified. It is deterministic from the content, but it is not a CRC32, not a sum
and not a size. We currently send zeros and the watch has accepted every write, which is already
informative.

Tell us when you have a spare moment and we will send exact commands for these - they need a
small change to the tool first, and we would rather write that than have you edit anything by
hand.

---

## How to send results

Plain text is perfect. For terminal output, select it and paste it into the message.

Two habits that save time:

- for anything involving Bluetooth, use `--redact`, as in task 1;
- when a command fails, send **all** of its output, not just the last line. The interesting
  part is usually in the middle.

## If you are stuck

Send the command you typed and everything it printed. There is no wrong question, and a
command that fails is information, not a mistake.
