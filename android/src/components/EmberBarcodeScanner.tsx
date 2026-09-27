import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Platform, PermissionsAndroid, TurboModuleRegistry } from 'react-native';
import { Camera, CameraType } from 'react-native-camera-kit';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';

// Camera barcode reader for Ember's meal search (issue #20, 2026-09-27) - the phone twin of the
// desktop's webcam EmberScanView.qml. react-native-camera-kit (MIT, Tesla): CameraX + ML Kit's
// bundled barcode model on Android (no Play Services needed), AVFoundation on iOS. Grocery codes
// only (EAN-13/8, UPC-A/E; iOS reports UPC-A as EAN-13 with a leading 0, which Open Food Facts
// accepts). onCode fires once, then the parent closes this view.

const GROCERY_CODES = Platform.OS === 'ios'
  ? (['ean-13', 'ean-8', 'upc-e'] as const)
  : (['ean-13', 'ean-8', 'upc-a', 'upc-e'] as const);

type Status = 'asking' | 'granted' | 'denied';

async function askCamera(): Promise<boolean> {
  if (Platform.OS === 'android') {
    const r = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.CAMERA, {
      title: 'Camera', message: 'Sommet uses the camera only to read a food barcode for your meal log.',
      buttonPositive: 'OK',
    });
    return r === PermissionsAndroid.RESULTS.GRANTED;
  }
  // iOS: camera-kit's JS helper only *checks*; its native module does the real request.
  const mod: any = TurboModuleRegistry.get('RNCameraKitModule');
  return mod ? !!(await mod.requestDeviceCameraAuthorization()) : true;
}

export function EmberBarcodeScanner({ onCode }: { onCode: (code: string) => void }) {
  const t = useV3Theme();
  const [status, setStatus] = useState<Status>('asking');
  const done = useRef(false);

  useEffect(() => {
    let alive = true;
    askCamera().then(ok => { if (alive) setStatus(ok ? 'granted' : 'denied'); }).catch(() => alive && setStatus('denied'));
    return () => { alive = false; };
  }, []);

  return (
    <View style={[styles.box, { borderRadius: v3Radius.small }]}>
      {status === 'granted' && (
        <Camera
          style={StyleSheet.absoluteFill}
          cameraType={CameraType.Back}
          scanBarcode
          allowedBarcodeTypes={[...GROCERY_CODES]}
          showFrame
          frameColor={t.success}
          laserColor={t.success}
          scanThrottleDelay={600}
          onReadCode={e => {
            const code = e.nativeEvent.codeStringValue?.trim();
            if (!code || done.current) return;
            done.current = true;
            onCode(code);
          }}
        />
      )}
      <Text style={[styles.hint, { fontSize: v3Type.caption }]}>
        {status === 'asking' ? 'Starting the camera…'
          : status === 'denied' ? 'Camera access is off - allow it in the system settings, or type the barcode.'
          : 'Point at the barcode.'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { height: 260, backgroundColor: '#000', overflow: 'hidden', justifyContent: 'flex-end', marginTop: v3Spacing.small },
  hint: { color: '#fff', textAlign: 'center', padding: v3Spacing.small, backgroundColor: '#0006' },
});
