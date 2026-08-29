package com.ambitsyncmodern

import android.content.Intent
import android.hardware.usb.UsbManager
import com.ambitsyncmodern.usb.AmbitUsbModule
import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {

  /**
   * Returns the name of the main component registered from JavaScript. This is used to schedule
   * rendering of the component.
   */
  override fun getMainComponentName(): String = "Sommet"

  /**
   * Returns the instance of the [ReactActivityDelegate]. We use [DefaultReactActivityDelegate]
   * which allows you to enable New Architecture with a single boolean flags [fabricEnabled]
   */
  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)

  // launchMode="singleTask" (AndroidManifest.xml) + the USB_DEVICE_ATTACHED
  // intent-filter means plugging in the watch while the app is already
  // running routes here instead of a fresh onCreate/launch. Forward it to JS
  // so HomeScreen can auto-sync. The cold-launch case (app not running yet)
  // is instead queried by JS on mount -- see AmbitUsbModule.wasLaunchedViaUsbAttach().
  override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    setIntent(intent)
    if (intent.action == UsbManager.ACTION_USB_DEVICE_ATTACHED) {
      AmbitUsbModule.notifyUsbAttached()
    }
  }
}
