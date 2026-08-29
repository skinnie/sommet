//
//  GarminModule.mm — iOS stub for the Garmin eTrex (USB mass-storage) module.
//
//  Garmin eTrex support is USB-only (the watch mounts as a USB drive), which
//  iOS cannot access. This stub registers as NativeModules.GarminModule so
//  src/native/GarminModule.ts's presence check passes and the app boots; every
//  operation rejects with GARMIN_USB_UNSUPPORTED_IOS. It is an RCTEventEmitter
//  (supporting GarminMountWaiting) so the JS NativeEventEmitter wiring is happy.
//
#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>

@interface GarminModule : RCTEventEmitter <RCTBridgeModule>
@end

@implementation GarminModule

RCT_EXPORT_MODULE(GarminModule);
+ (BOOL)requiresMainQueueSetup { return NO; }
- (NSArray<NSString *> *)supportedEvents { return @[@"GarminMountWaiting"]; }

static void reject_ios(RCTPromiseRejectBlock reject) {
  reject(@"GARMIN_USB_UNSUPPORTED_IOS", @"Garmin eTrex uses USB mass storage, which is not available on iOS", nil);
}

RCT_EXPORT_METHOD(connect:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }
RCT_EXPORT_METHOD(disconnect:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve(@YES); }
RCT_EXPORT_METHOD(listActivityFiles:(double)volumeIndex resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }
RCT_EXPORT_METHOD(readActivityFile:(double)volumeIndex fileName:(NSString *)fileName resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }
RCT_EXPORT_METHOD(listGpxDirFiles:(double)volumeIndex resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }
RCT_EXPORT_METHOD(readGpxDirFile:(double)volumeIndex fileName:(NSString *)fileName resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }
RCT_EXPORT_METHOD(writeGpxToSdCard:(double)volumeIndex fileName:(NSString *)fileName gpxContent:(NSString *)gpxContent resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject_ios(reject); }

@end
