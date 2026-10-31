// Watch jobs share one USB session (AmbitUsbModule.connect/disconnect, tablet audit F1,
// 2026-10-31): a job that starts while another holds the watch joins it instead of opening the
// watch again, and only the last job to finish closes it. Before, a second job replaced the
// native watch handle under the first, and the first one's disconnect closed the second's.
export {};

const mockLog: string[] = [];
const mockListeners: Record<string, () => void> = {};
const mockNative = {
  fail: false,
  connect: jest.fn(async () => {
    mockLog.push('open');
    if (mockNative.fail) { const e: any = new Error('No Suunto watch detected'); e.code = 'AMBIT_NOT_FOUND'; throw e; }
    return { name: 'Suunto Ambit3 Peak', vendorId: 0x1493, productId: 0x1c };
  }),
  disconnect: jest.fn(async () => { mockLog.push('close'); return true; }),
};

jest.mock('react-native-fs', () => ({ DocumentDirectoryPath: '/files' }));
jest.mock('react-native', () => ({
  NativeModules: { AmbitUsbModule: mockNative },
  NativeEventEmitter: class {
    addListener(name: string, cb: () => void) { mockListeners[name] = cb; return { remove() {} }; }
  },
  Platform: { OS: 'android' },
}));

const usb = require('../../native/AmbitUsbModule') as typeof import('../../native/AmbitUsbModule');

beforeEach(async () => {
  // Close whatever a previous test left open, then start from a clean log.
  for (let i = 0; i < 5; i++) await usb.disconnect();
  mockLog.length = 0;
  mockNative.fail = false;
});

test('one job opens the watch and closes it', async () => {
  await usb.connect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'close']);
});

test('a job that starts while another holds the watch joins it; the last one closes', async () => {
  await usb.connect();            // the sync
  await usb.connect();            // the activity-class update, started meanwhile
  await usb.disconnect();         // the sync ends: the update still has the watch
  expect(mockLog).toEqual(['open']);
  await usb.disconnect();         // the update ends
  expect(mockLog).toEqual(['open', 'close']);
});

test('two jobs starting at the same moment open the watch once', async () => {
  const [a, b] = await Promise.all([usb.connect(), usb.connect()]);
  expect(a.name).toBe('Suunto Ambit3 Peak');
  expect(b.name).toBe('Suunto Ambit3 Peak');
  await usb.disconnect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'close']);
});

test('a job inside another job does not wait for it', async () => {
  await usb.connect();
  await usb.connect();            // nested: must resolve straight away
  await usb.disconnect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'close']);
});

test('after the last job, the next one opens the watch again', async () => {
  await usb.connect();
  await usb.disconnect();
  await usb.connect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'close', 'open', 'close']);
});

test('a plug-in event makes the next job open a fresh handle, and one close ends it all', async () => {
  await usb.connect();
  mockListeners.AmbitUsbAttached();   // the clip bounced: the old handle belongs to the old device
  await usb.connect();
  expect(mockLog).toEqual(['open', 'open']);
  await usb.disconnect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'open', 'close']);
});

test('a failed open leaves nothing counted, and the next job tries again', async () => {
  mockNative.fail = true;
  await expect(usb.connect()).rejects.toThrow('No Suunto watch');
  mockNative.fail = false;
  await usb.connect();
  await usb.disconnect();
  expect(mockLog).toEqual(['open', 'open', 'close']);
});
