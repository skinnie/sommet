jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
import { foodFromOff, foodFromUsda, isBarcode, portion, rankFoods, Food } from '../EmberFood';

// Shapes and values from the live APIs, 2026-09-27.
const NUTELLA = {   // world.openfoodfacts.org/api/v2/product/3017620422003 -> .product
  code: '3017620422003', product_name: 'Nutella', brands: 'Nutella, Ferrero',
  nutriments: { 'energy-kcal_100g': 539, proteins_100g: 6.3, carbohydrates_100g: 57.5, fat_100g: 30.9 },
};
const SKYR_HIT = {  // search.openfoodfacts.org hit: brands is a list here
  code: '8710624358174', product_name: 'Skyr naturel', brands: ['Skyr'],
  nutriments: { 'energy-kcal_100g': 62, proteins_100g: 11, carbohydrates_100g: 4, fat_100g: 0.2 },
};
const BANANA = {    // api.nal.usda.gov/fdc/v1/foods/search, SR Legacy
  fdcId: 173944, description: 'Bananas, raw', dataType: 'SR Legacy',
  foodNutrients: [
    { nutrientId: 1008, value: 89.0 }, { nutrientId: 1003, value: 1.09 },
    { nutrientId: 1005, value: 22.8 }, { nutrientId: 1004, value: 0.33 },
  ],
};

describe('normalising', () => {
  it('Open Food Facts product -> per 100 g, first brand', () => {
    expect(foodFromOff(NUTELLA)).toEqual({
      source: 'off', id: '3017620422003', name: 'Nutella', brand: 'Nutella',
      kcal100: 539, protein100: 6.3, carbs100: 57.5, fat100: 30.9, servingG: undefined,
    });
    expect(foodFromOff(SKYR_HIT)!.brand).toBe('Skyr');
  });
  it('falls back to kJ, and drops products without energy', () => {
    expect(foodFromOff({ product_name: 'X', nutriments: { 'energy-kj_100g': 418.4 } })!.kcal100).toBeCloseTo(100, 6);
    expect(foodFromOff({ product_name: 'X', nutriments: {} })).toBeNull();
  });
  it('USDA food -> per 100 g; Foundation foods may only have Atwater energy (2047)', () => {
    expect(foodFromUsda(BANANA)).toMatchObject({ source: 'usda', id: '173944', name: 'Bananas, raw', kcal100: 89, carbs100: 22.8 });
    expect(foodFromUsda({ fdcId: 1, description: 'Y', foodNutrients: [{ nutrientId: 2047, value: 85 }] })!.kcal100).toBe(85);
  });
});

describe('portion', () => {
  it('scales to the grams eaten, rounded', () => {
    expect(portion(foodFromUsda(BANANA)!, 120)).toEqual({ kcal: 107, protein: 1, carbs: 27, fat: 0 });
    expect(portion(foodFromOff(NUTELLA)!, 15)).toEqual({ kcal: 81, protein: 1, carbs: 9, fat: 5 });
  });
});

describe('isBarcode', () => {
  it('8-14 digits only', () => {
    expect(isBarcode('3017620422003')).toBe(true);
    expect(isBarcode('skyr')).toBe(false);
    expect(isBarcode('1234')).toBe(false);
  });
});

describe('rankFoods', () => {
  const f = (name: string, brand?: string): Food => ({ source: 'off', id: name, name, brand, kcal100: 1 });
  it('puts the plain generic food first - real "banana raw" results, 2026-09-27', () => {
    const out = rankFoods([
      f('Organic Banana Raw Crunch Granola', 'Rawcology'), f('Proteline', 'Banana'),
      f('Raw Protein Banana', 'Bombus'), f('Bananas, raw', 'USDA'),
    ], 'banana raw');
    expect(out.map(x => x.name)).toEqual(['Bananas, raw', 'Raw Protein Banana', 'Organic Banana Raw Crunch Granola', 'Proteline']);
  });
});
