//
//  AmbitBleModule.m — RN bridge registration for the Swift AmbitBleModule.
//
//  Exposes the Swift @objc methods to React Native under the module name
//  "AmbitBleModule" (NativeModules.AmbitBleModule), matching
//  src/native/AmbitBleModule.ts's `const { AmbitBleModule } = NativeModules`.
//  The Swift class is RCTEventEmitter, so the "AmbitBleDisconnected" event is
//  delivered through the same NativeEventEmitter the JS wrapper subscribes to.
//
#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>

@interface RCT_EXTERN_MODULE(AmbitBleModule, RCTEventEmitter)

RCT_EXTERN_METHOD(scanAndConnect:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(scanAndConnectTo:(NSString *)address
                  resolver:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(listBondedWatches:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(disconnectBle:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

@end
