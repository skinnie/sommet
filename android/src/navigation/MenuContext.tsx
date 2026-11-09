import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { LayoutAnimation, Platform, UIManager, useWindowDimensions } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { IconName } from '../components/ui/Icon';
import {
  MenuStyle, MenuFavourites, DEFAULT_FAVOURITES, resolveStyle, isTabletSize, parseFavourites, itemsKey,
} from './menuModel';

// What the menu needs, shared by every page: the style this device uses, the places picked for
// the fan and the bar, and the list of places. Home builds that list (which places show depends on
// what is connected) and hands it over here, so the menu can be drawn above every page.

export interface MenuItem {
  id: string;
  label: string;
  icon: IconName;
  onPress: () => void;
  group?: 'training' | 'watch' | 'adv';
}

// The side menu folds with a layout animation (as it did in the old NavShell).
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const STYLE_KEY = 'sommet:menuStyle';
const FAV_KEY = 'sommet:menuFavourites';

interface MenuValue {
  style: MenuStyle;
  setStyle: (s: MenuStyle) => void;
  isTablet: boolean;
  favourites: MenuFavourites;
  setFavourites: (f: MenuFavourites) => void;
  items: MenuItem[];                                   // for drawing
  itemsRef: React.MutableRefObject<MenuItem[]>;        // latest tap handlers
  publishItems: (items: MenuItem[]) => void;
  railOpen: boolean;                                   // the tablet's side menu, open or folded
  toggleRail: () => void;
}

const MenuContext = createContext<MenuValue | null>(null);

export function MenuProvider({ children }: { children: React.ReactNode }) {
  const { width, height } = useWindowDimensions();
  const isTablet = isTabletSize(width, height);
  const [stored, setStored] = useState<string | null>(null);
  const [favourites, setFavState] = useState<MenuFavourites>(DEFAULT_FAVOURITES);
  const [items, setItems] = useState<MenuItem[]>([]);
  const itemsRef = useRef<MenuItem[]>([]);
  const keyRef = useRef('');
  const [railOpen, setRailOpen] = useState(true);

  useEffect(() => {
    AsyncStorage.multiGet([STYLE_KEY, FAV_KEY]).then(([[, s], [, f]]) => {
      setStored(s);
      setFavState(parseFavourites(f));
    }).catch(() => {});
  }, []);

  const style = resolveStyle(stored, isTablet);

  const setStyle = useCallback((s: MenuStyle) => {
    setStored(s);
    AsyncStorage.setItem(STYLE_KEY, s).catch(() => {});
  }, []);

  const setFavourites = useCallback((f: MenuFavourites) => {
    setFavState(f);
    AsyncStorage.setItem(FAV_KEY, JSON.stringify(f)).catch(() => {});
  }, []);

  // Called after every Home render: the tap handlers are always the latest, but the menu is only
  // redrawn when a place comes, goes or changes name.
  const publishItems = useCallback((next: MenuItem[]) => {
    itemsRef.current = next;
    const k = itemsKey(next);
    if (k !== keyRef.current) { keyRef.current = k; setItems(next); }
  }, []);

  const toggleRail = useCallback(() => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setRailOpen(o => !o);
  }, []);

  const value = useMemo(() => ({
    style, setStyle, isTablet, favourites, setFavourites, items, itemsRef, publishItems, railOpen, toggleRail,
  }), [style, setStyle, isTablet, favourites, setFavourites, items, publishItems, railOpen, toggleRail]);

  return <MenuContext.Provider value={value}>{children}</MenuContext.Provider>;
}

export function useNavMenu(): MenuValue {
  const v = useContext(MenuContext);
  if (!v) throw new Error('useNavMenu needs a MenuProvider above it');
  return v;
}
