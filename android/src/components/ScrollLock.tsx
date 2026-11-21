// A page's scrolling can be paused by a map inside it (André, 2026-11-21: "press and drag").
// The desktop does the same (MapView.qml switches its page's Flickable off while a map is held):
// a finger held still on a map grabs it, and the page must stop scrolling so the drag moves the
// map instead. On Android the native ScrollView takes a moving finger before any JS handler
// sees it, so the only reliable way is to turn its scrolling off while the map is held.
import React, { createContext, forwardRef, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { ScrollView, ScrollViewProps } from 'react-native';

type Lock = { lock: () => void; unlock: () => void };
const noop: Lock = { lock: () => {}, unlock: () => {} };
const ScrollLockContext = createContext<Lock>(noop);

/** The page's lock, or a no-op when the map is not inside a LockableScrollView. */
export function useScrollLock(): Lock {
  return useContext(ScrollLockContext);
}

/** A ScrollView whose scrolling a map inside it can pause (same props as ScrollView). */
export const LockableScrollView = forwardRef<ScrollView, ScrollViewProps>((props, ref) => {
  const [locked, setLocked] = useState(false);
  const holders = useRef(0);
  const lock = useCallback(() => { holders.current += 1; setLocked(true); }, []);
  const unlock = useCallback(() => {
    holders.current = Math.max(0, holders.current - 1);
    if (holders.current === 0) setLocked(false);
  }, []);
  const value = useMemo(() => ({ lock, unlock }), [lock, unlock]);
  return (
    <ScrollLockContext.Provider value={value}>
      <ScrollView ref={ref} {...props} scrollEnabled={!locked && props.scrollEnabled !== false} />
    </ScrollLockContext.Provider>
  );
});
