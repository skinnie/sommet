// Backup opens from Settings, which doesn't know the watch (tablet audit F22, 2026-10-31). The
// backup now asks the watch what it is: the Peak's swim styles get saved, and an Ambit1/2 is
// backed up its own way instead of the Ambit3 way.
export {};

const mockWritten: string[] = [];
const mockLog: string[] = [];
let mockModel = 'Emu';

jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/files',
  exists: jest.fn(async () => true),
  mkdir: jest.fn(async () => {}),
  writeFile: jest.fn(async (p: string) => { mockWritten.push(p.split('/').pop()!); }),
}));
jest.mock('../../native/AmbitUsbModule', () => ({
  connect: jest.fn(async () => { mockLog.push('connect'); return {}; }),
  disconnect: jest.fn(async () => { mockLog.push('disconnect'); }),
  readRegion: jest.fn(async () => { mockLog.push('readRegion'); return 'AAAA'; }),
  saveFileAs: jest.fn(),
  getDeviceInfo: jest.fn(async () => ({ model: mockModel, serial: '8A153C5111000900' })),
}));
jest.mock('../MemoryMap', () => ({
  readNavBases: jest.fn(async () => ({ waypointBase: 1, waypointSize: 2, routeBase: 3, routeSize: 4 })),
}));
jest.mock('../AmbitSettingsService', () => ({ isAmbit12: jest.fn(() => false) }));
jest.mock('../AmbitLegacyNav', () => ({
  backupLegacyRoutes: jest.fn(async () => { mockLog.push('legacyRoutes'); return { bytes: new Uint8Array([1]) }; }),
  backupLegacyWaypoints: jest.fn(async () => { mockLog.push('legacyWaypoints'); return [{ name: 'A' }]; }),
  restoreLegacyRoutes: jest.fn(),
  restoreLegacyWaypoints: jest.fn(),
}));
jest.mock('../SwimStyles', () => ({
  readSwimStyles: jest.fn(async (model?: string) =>
    model === 'Emu' ? [{ id: 2, style: 'Breaststroke', vectors: [3, 4, 5, 6] }] : null),
  writeSwimStyles: jest.fn(),
  isTaught: (s: any) => s.vectors.some((v: number) => v !== 0),
}));

const { createNavBackup } = require('../NavBackupService') as typeof import('../NavBackupService');

beforeEach(() => { mockWritten.length = 0; mockLog.length = 0; });

test('without a model, the Peak is asked and its swim styles are saved', async () => {
  mockModel = 'Emu';
  await createNavBackup(undefined);
  expect(mockWritten.some(f => f.endsWith('_swim-styles.json'))).toBe(true);
  expect(mockWritten.some(f => f.endsWith('_routes.bin'))).toBe(true);
});

test('without a model, an Ambit2 is backed up its own way, not the Ambit3 way', async () => {
  mockModel = 'Duck';
  await createNavBackup(undefined);
  expect(mockLog).toContain('legacyRoutes');
  expect(mockLog).not.toContain('readRegion');
  expect(mockWritten.some(f => f.endsWith('_legacy-routes.bin'))).toBe(true);
});

test('a model handed in is used as it is, without asking the watch first', async () => {
  mockModel = 'Duck';
  await createNavBackup('Emu');
  expect(mockLog).toContain('readRegion');
  expect(mockLog).not.toContain('legacyRoutes');
});
