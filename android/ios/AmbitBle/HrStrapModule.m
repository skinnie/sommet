//
//  HrStrapModule.m — RN bridge for the Swift HrStrapModule.
//
//  Exposes measure() to React Native under the module name "HrStrap"
//  (NativeModules.HrStrap), matching src/services/HrStrapService.ts.
//
#import <React/RCTBridgeModule.h>

@interface RCT_EXTERN_MODULE(HrStrap, NSObject)

RCT_EXTERN_METHOD(measure:(nonnull NSNumber *)seconds
                  nameFilter:(NSString *)nameFilter
                  resolver:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

@end
