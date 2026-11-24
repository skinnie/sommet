import { placeFromLookup, usableNetworks } from '../DeviceLocation';

jest.mock('react-native', () => ({ NativeModules: {}, PermissionsAndroid: {}, Platform: { OS: 'android' } }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(), setItem: jest.fn() }));

// What of the Wi-Fi networks heard may go to BeaconDB (André, 2026-11-23).
describe('usableNetworks', () => {
  it('keeps real networks, strongest first, and leaves out the others', () => {
    const out = usableNetworks([
      { bssid: '00:11:22:33:44:55', level: -70, ssid: 'Home' },
      { bssid: '00:11:22:33:44:66', level: -40, ssid: 'Cafe' },
      { bssid: '02:11:22:33:44:77', level: -30, ssid: 'Phone hotspot' },   // made-up address
      { bssid: '00:11:22:33:44:88', level: -50, ssid: 'Mine_nomap' },      // asks not to be mapped
      { bssid: 'not an address', level: -20 },
    ]);
    expect(out).toEqual([
      { macAddress: '00:11:22:33:44:66', signalStrength: -40 },
      { macAddress: '00:11:22:33:44:55', signalStrength: -70 },
    ]);
  });
  it('sends at most twenty', () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ bssid: `00:11:22:33:44:${(16 + i).toString(16)}`, level: -i }));
    expect(usableNetworks(many)).toHaveLength(20);
  });
});

describe('placeFromLookup', () => {
  it('takes a Wi-Fi answer', () => {
    expect(placeFromLookup({ location: { lat: 39.7, lng: -8.8 }, accuracy: 80 }))
      .toEqual({ lat: 39.7, lon: -8.8, accuracy: 80, ageSeconds: 0 });
  });
  it('refuses an answer that fell back to the IP address', () => {
    expect(placeFromLookup({ location: { lat: 39.6, lng: -8.8 }, accuracy: 25000, fallback: 'ipf' })).toBeNull();
    expect(placeFromLookup({ error: { code: 404 } })).toBeNull();
  });
});
