# Building Sommet for iPhone / iPad

This is a step-by-step guide for building the **iOS** app from source and installing it
on a physical iPhone. It assumes no prior React Native or iOS knowledge.

> **The one hard requirement: a Mac.** iOS apps can *only* be compiled on macOS with
> Apple's Xcode toolchain. There is no way to build an iPhone app on Linux or Windows,
> and GitHub cannot build or install it for you (the repo's CI only builds the
> Linux/Android/desktop artifacts). Cloning the repo is step one; the build happens on a Mac.

Sommet's iOS build talks to the **BLE watches** (Ambit3 / Traverse / Kailash) over
Bluetooth. Ambit1/2 are USB-only and are not reachable from an iPhone — they stay
desktop/Android-only.

---

## 1. What you need

| Thing | Version / note | How to get it |
|---|---|---|
| **A Mac** | macOS recent enough for the Xcode below | — |
| **Xcode** | 16 or newer (26.x is fine) | Mac App Store, then open it once to install components |
| **Xcode Command Line Tools** | matches Xcode | `xcode-select --install` |
| **Node.js** | **≥ 22.11.0** (see `android/package.json` `engines`) | [nodejs.org](https://nodejs.org) or `brew install node` / `nvm` |
| **CocoaPods** | current | `sudo gem install cocoapods` (or `brew install cocoapods`) |
| **An Apple ID** | any free Apple ID works to start | you already have one |
| **The iPhone + its USB cable** | iOS 15.1 or newer | the app's minimum is iOS 15.1 |

Check the tools are present:

```bash
xcodebuild -version      # Xcode 16+  
node --version           # v22.11.0 or higher
pod --version            # any recent
```

---

## 2. Get the code

```bash
git clone https://github.com/skinnie/sommet.git
cd sommet
git checkout main        # main is the current, canonical branch
```

The React Native project root is the **`android/`** folder (historical name — it holds the
whole cross-platform RN app, including `android/ios/`).

---

## 3. Install dependencies

```bash
cd android
npm install              # installs JS deps; also runs patch-package automatically

cd ios
pod install              # installs the native iOS pods (uses the pinned Podfile.lock)
```

`pod install` can take a few minutes the first time. When it finishes it will tell you to
open the **`.xcworkspace`**, not the `.xcodeproj` — that matters (the workspace is what
links in the pods).

---

## 4. Open the project

```bash
open android/ios/Sommet.xcworkspace
```

Xcode opens. In the left sidebar, click the top **Sommet** project, then select the
**Sommet** target.

---

## 5. Set up signing (this is what puts it on the phone)

Apple requires every app installed on a device to be *code-signed*. For a personal build
onto your own iPhone, a **free Apple ID** is enough.

1. Xcode → **Settings… → Accounts → “+” → Apple ID** → sign in with your Apple ID.
2. Back in the project, select the **Sommet** target → **Signing & Capabilities** tab.
3. Tick **Automatically manage signing**.
4. **Team**: pick your name — it appears as *“(Your Name) — Personal Team”*.
5. If Xcode complains the bundle identifier is taken, change **Bundle Identifier** from
   `com.skinnie.sommet` to something unique like `com.yourname.sommet`. (Only matters for
   *your* signed copy; it doesn't change the app.)

Xcode will create a free provisioning profile automatically. No paid account needed yet.

---

## 6. Build & run onto the iPhone

1. Plug the iPhone into the Mac with the cable.
2. On the iPhone: **unlock it**, and if asked, tap **Trust This Computer**.
3. In Xcode's top toolbar, click the device dropdown (next to the Sommet scheme) and pick
   **your iPhone** (not a simulator — BLE + the watch only work on a real device).
4. Press the **▶ Run** button (or `⌘R`).

First run, one extra step Apple requires:

5. The app installs but iOS blocks launching an app from an unknown developer. On the
   iPhone go to **Settings → General → VPN & Device Management → (your Apple ID) → Trust**.
6. Now tap the Sommet icon on the home screen. When it asks, **allow Bluetooth** — that's
   required to find the watch.

That's it. The app is on the phone.

---

## 7. ⏳ The 7-day expiry (free Apple ID) — how to keep using it

A **free** Apple ID can only sign apps with a **7-day** certificate. After 7 days the app
stops launching (“the app could not be verified” / it just won't open).

**You do NOT need to change any code or push an update.** The app is fine — only the
signature expired. To renew it:

- Plug the iPhone back into the Mac, open the workspace, and press **▶ Run** again.
- Xcode re-signs with a fresh 7-day profile and reinstalls **over** the existing app.
- Your data is preserved (don't delete the app — just reinstall on top).

So: **once a week, plug in and hit Run.** Nothing else.

Free-tier limits worth knowing: 7-day signing, max 3 side-loaded apps per device, and you
must be able to reach the Mac to renew.

---

## 8. Avoiding the weekly renewal — paid options

If the weekly reinstall is annoying, an **Apple Developer Program** membership ($99/year)
removes it:

| Option | What you get | Best for |
|---|---|---|
| **Paid + Xcode install** | Signing profile valid **~1 year** — reinstall roughly once a year instead of weekly | One phone, cable access |
| **TestFlight** | Install **over the air** from the TestFlight app, no cable; builds last **90 days**; can add other testers | Installing remotely / on several phones |
| **CI-built signed `.ipa`** | A GitHub Actions macOS runner builds and signs automatically on each push (requires uploading the signing cert + profile as repo secrets) | Hands-off rebuilds — *not set up in this repo yet* |

For remote use, **TestFlight** is usually the nicest: build once, and the phone updates
itself from the TestFlight app with no Mac in the loop for 90 days at a time.

---

## 9. Troubleshooting

- **`pod install` fails** → make sure you ran `npm install` in `android/` first (pods
  depend on the node modules), then retry. `cd android/ios && pod install --repo-update`.
- **“No such module” / build errors after pulling** → deps changed: re-run `npm install`
  then `pod install`.
- **“Untrusted Developer” on the phone** → Settings → General → VPN & Device Management →
  trust your Apple ID (step 5 above).
- **Device not showing in Xcode** → unlock the phone, tap *Trust This Computer*, and make
  sure the cable is data-capable.
- **Metro / JS bundler** → for a dev build Xcode starts Metro automatically; if it doesn't,
  run `npm start` in `android/` in a separate terminal. A **Release** build (Xcode →
  Product → Scheme → Edit Scheme → Run → Build Configuration → Release) bundles the JS in
  and needs no Metro — better for a build you'll actually use day to day.
- **App won't open after ~a week** → that's the 7-day expiry (section 7): plug in, Run again.

---

## Reference — project facts

- **Repo:** github.com/skinnie/sommet · branch **main**
- **RN app root:** `android/` · **iOS project:** `android/ios/`
- **Workspace:** `android/ios/Sommet.xcworkspace` · **Scheme:** `Sommet`
- **Bundle ID:** `com.skinnie.sommet` (change for a personal free-signed build)
- **React Native:** 0.84.1 · **Node:** ≥ 22.11.0 · **iOS deployment target:** 15.1
- **Watches over iOS:** Ambit3 / Traverse / Kailash (BLE only)
