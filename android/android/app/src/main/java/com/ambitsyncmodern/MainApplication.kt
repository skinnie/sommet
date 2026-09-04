package com.ambitsyncmodern

import android.app.Application
import com.facebook.react.PackageList
import com.facebook.react.ReactApplication
import com.ambitsyncmodern.usb.AmbitUsbPackage
import com.ambitsyncmodern.ble.AmbitBlePackage
import com.ambitsyncmodern.garmin.GarminPackage
import com.ambitsyncmodern.smartsensor.AmbitSmartSensorPackage
import com.ambitsyncmodern.hrstrap.HrStrapPackage
import com.ambitsyncmodern.catalog.AmbitCatalogPackage
import com.facebook.react.ReactHost
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.load
import com.facebook.react.defaults.DefaultReactHost.getDefaultReactHost
import com.facebook.react.soloader.OpenSourceMergedSoMapping
import com.facebook.soloader.SoLoader

class MainApplication : Application(), ReactApplication {

  override val reactHost: ReactHost by lazy {
    getDefaultReactHost(
      context = applicationContext,
      packageList =
        PackageList(this).packages.apply {
          add(AmbitUsbPackage())
          add(AmbitBlePackage())
          add(GarminPackage())
          add(AmbitSmartSensorPackage())
          add(HrStrapPackage())
          add(AmbitCatalogPackage())
        },
    )
  }

  override fun onCreate() {
    super.onCreate()
    SoLoader.init(this, OpenSourceMergedSoMapping)
    load()
  }
}
