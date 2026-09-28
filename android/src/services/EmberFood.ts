// Ember food search - fill a meal's kcal/macros from a food database instead of typing them
// (André, 2026-09-27, "go for ember"; issue #20). Twin of desktop/src/services/emberfoodservice.cpp.
//
// Idea from SparkyFitness's food lookups, but NOT its code: SparkyFitness is non-commercial-only,
// which can't be combined with this app's GPL-3.0. We query the same free databases directly:
//   - Open Food Facts (ODbL, attribution): packaged products, by name (search.openfoodfacts.org)
//     or by barcode (world.openfoodfacts.org/api/v2/product/<code>). No key; identifying UA.
//   - USDA FoodData Central (public domain / CC0): generic foods ("banana, raw"), Foundation +
//     SR Legacy, all per 100 g. The user's own free key when set in Settings, else DEMO_KEY.
// Every result is normalised to per-100 g values; portion() scales to the grams eaten. The last
// foods picked are kept on the device, so repeat meals work offline.

import AsyncStorage from '@react-native-async-storage/async-storage';

export interface Food {
  source: 'off' | 'usda';
  id: string;            // OFF barcode / USDA fdcId
  name: string;
  brand?: string;
  kcal100: number;
  protein100?: number;
  carbs100?: number;
  fat100?: number;
  servingG?: number;     // the label's serving size, when OFF has one
}

export interface Portion { kcal: number; protein: number; carbs: number; fat: number }

const UA = 'Sommet/0.2 (https://github.com/skinnie/sommet)';
const RECENT_KEY = 'ember.recentFoods';
const USDA_KEY = 'ember.usdaApiKey';

/** Personal USDA FoodData Central key (free, api.data.gov), set in Settings. Empty = the shared
 *  DEMO_KEY, ~30 searches an hour per network (André, 2026-09-28). Stays on the device. */
export async function getUsdaApiKey(): Promise<string> {
  try { return ((await AsyncStorage.getItem(USDA_KEY)) ?? '').trim(); } catch { return ''; }
}
export async function setUsdaApiKey(key: string): Promise<void> {
  const k = key.trim();
  try { if (k) await AsyncStorage.setItem(USDA_KEY, k); else await AsyncStorage.removeItem(USDA_KEY); } catch { /* best-effort */ }
}
const RECENT_MAX = 30;

const num = (v: unknown): number | undefined => {
  const n = typeof v === 'string' ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) ? n : undefined;
};

/** One Open Food Facts product (search hit or /product answer) -> Food, or null without kcal. */
export function foodFromOff(p: any): Food | null {
  const n = p?.nutriments ?? {};
  // Some products only carry kJ: 1 kcal = 4.184 kJ.
  const kcal = num(n['energy-kcal_100g']) ?? (num(n['energy-kj_100g']) !== undefined ? num(n['energy-kj_100g'])! / 4.184 : undefined);
  const name = String(p?.product_name ?? '').trim();
  if (kcal === undefined || !name) return null;
  const brands = Array.isArray(p?.brands) ? p.brands.join(', ') : p?.brands;
  return {
    source: 'off', id: String(p?.code ?? ''), name,
    brand: brands ? String(brands).split(',').map((s: string) => s.trim()).filter(Boolean)[0] : undefined,
    kcal100: kcal, protein100: num(n.proteins_100g), carbs100: num(n.carbohydrates_100g), fat100: num(n.fat_100g),
    servingG: num(p?.serving_quantity),
  };
}

/** One USDA FDC search food -> Food (nutrient ids: 1008 energy kcal, 2047/2048 Atwater energy for
 *  Foundation foods, 1003 protein, 1005 carbs, 1004 fat). */
export function foodFromUsda(f: any): Food | null {
  const byId = new Map<number, number>();
  for (const x of f?.foodNutrients ?? []) {
    const v = num(x?.value);
    if (typeof x?.nutrientId === 'number' && v !== undefined) byId.set(x.nutrientId, v);
  }
  const kcal = byId.get(1008) ?? byId.get(2047) ?? byId.get(2048);
  const name = String(f?.description ?? '').trim();
  if (kcal === undefined || !name) return null;
  return {
    source: 'usda', id: String(f?.fdcId ?? ''), name, brand: 'USDA',
    kcal100: kcal, protein100: byId.get(1003), carbs100: byId.get(1005), fat100: byId.get(1004),
  };
}

export function portion(food: Food, grams: number): Portion {
  const k = Math.max(0, grams) / 100;
  return {
    kcal: Math.round(food.kcal100 * k),
    protein: Math.round((food.protein100 ?? 0) * k),
    carbs: Math.round((food.carbs100 ?? 0) * k),
    fat: Math.round((food.fat100 ?? 0) * k),
  };
}

/** Relevance: query words found at the start of a word in the name (only in the brand: half), then
 *  shorter names first - so "banana raw" puts USDA's "Bananas, raw" above branded granolas.
 *  Same rule as the desktop's EmberFoodService::rank(). */
export function rankFoods(foods: Food[], query: string): Food[] {
  const words = query.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const found = (text: string, w: string) =>
    text.toLowerCase().split(/[^\p{L}\p{N}]+/u).some(t => t && t.startsWith(w));
  // A word matched by the name counts 1; one matched only by the brand counts 0.5.
  const score = (f: Food) => words.reduce((n, w) => n + (found(f.name, w) ? 1 : found(f.brand ?? '', w) ? 0.5 : 0), 0);
  return foods
    .map((f, i) => ({ f, i, s: score(f) }))
    .sort((a, b) => b.s - a.s || a.f.name.length - b.f.name.length || a.i - b.i)
    .map(x => x.f);
}

/** 8-14 digits = a barcode (EAN-8, UPC-A, EAN-13, GTIN-14); anything else is a name search. */
export const isBarcode = (q: string) => /^\d{8,14}$/.test(q.trim());

async function getJson(url: string): Promise<any | null> {
  try {
    const res = await fetch(url, { headers: { 'User-Agent': UA, Accept: 'application/json' } });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

export async function lookupBarcode(code: string): Promise<Food | null> {
  const body = await getJson(`https://world.openfoodfacts.org/api/v2/product/${encodeURIComponent(code.trim())}.json`
    + '?fields=code,product_name,brands,nutriments,serving_quantity');
  return body?.status === 1 && body.product ? foodFromOff({ ...body.product, code: body.product.code ?? code }) : null;
}

/** Name search in both databases, ranked by rankFoods(). */
export async function searchFoods(query: string): Promise<Food[]> {
  const q = query.trim();
  if (!q) return [];
  if (isBarcode(q)) { const f = await lookupBarcode(q); return f ? [f] : []; }
  const fields = 'code,product_name,brands,nutriments,serving_quantity';
  const usdaUrl = (key: string) => `https://api.nal.usda.gov/fdc/v1/foods/search?query=${encodeURIComponent(q)}`
    + `&api_key=${encodeURIComponent(key)}&pageSize=10&dataType=Foundation,SR%20Legacy`;
  const ownKey = await getUsdaApiKey();
  const [off, usdaOwn] = await Promise.all([
    getJson(`https://search.openfoodfacts.org/search?q=${encodeURIComponent(q)}&page_size=20&fields=${fields}`),
    getJson(usdaUrl(ownKey || 'DEMO_KEY')),
  ]);
  // A mistyped personal key is refused (HTTP 403): fall back to the demo key rather than lose
  // the generic foods silently.
  const usda = usdaOwn ?? (ownKey ? await getJson(usdaUrl('DEMO_KEY')) : null);
  const out: Food[] = [];
  for (const h of off?.hits ?? []) { const f = foodFromOff(h); if (f) out.push(f); }
  for (const x of usda?.foods ?? []) { const f = foodFromUsda(x); if (f) out.push(f); }
  return rankFoods(out, q);
}

export async function recentFoods(): Promise<Food[]> {
  try {
    const raw = await AsyncStorage.getItem(RECENT_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch { return []; }
}

export async function rememberFood(food: Food): Promise<void> {
  const list = (await recentFoods()).filter(f => !(f.source === food.source && f.id === food.id));
  list.unshift(food);
  try { await AsyncStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); } catch { /* best-effort */ }
}
