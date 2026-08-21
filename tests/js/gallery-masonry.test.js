/**
 * Unit tests for the pure helpers extracted from gallery-masonry.js:
 * date parsing, sort comparator, column count and item height estimate.
 */
const {
    parseGalleryDate,
    compareGalleryItemsByDate,
    getColumnCountForWidth,
    estimateGalleryItemHeight,
    MIN_COLUMN_WIDTH,
} = require('../../app/static/js/gallery-masonry.js');

describe('parseGalleryDate', () => {
    test('parses ISO strings with microseconds (Python datetime)', () => {
        expect(parseGalleryDate('2026-03-02T11:02:41.820010'))
            .toBe(Date.UTC(2026, 2, 2, 11, 2, 41, 820));
    });

    test('normalizes space-separated datetime', () => {
        expect(parseGalleryDate('2026-03-02 11:02:41'))
            .toBe(Date.UTC(2026, 2, 2, 11, 2, 41));
    });

    test('keeps explicit timezone offsets', () => {
        expect(parseGalleryDate('2026-03-02T11:02:41+03:00'))
            .toBe(Date.UTC(2026, 2, 2, 8, 2, 41));
    });

    test('returns 0 for missing or invalid input', () => {
        expect(parseGalleryDate('')).toBe(0);
        expect(parseGalleryDate(undefined)).toBe(0);
        expect(parseGalleryDate('not-a-date')).toBe(0);
    });
});

describe('compareGalleryItemsByDate', () => {
    const item = (dataset) => ({
        dataset,
        classList: { contains: () => false },
    });

    test('sorts items by upload date descending', () => {
        const items = [
            item({ uploadedAt: '2024-01-15', itemType: 'item' }),
            item({ uploadedAt: '2024-01-20', itemType: 'album' }),
        ];
        const sorted = [...items].sort((a, b) =>
            compareGalleryItemsByDate(a, b, 'uploaded'));
        expect(sorted[0].dataset.uploadedAt).toBe('2024-01-20');
    });

    test('taken mode prefers taken_at over uploaded_at', () => {
        const a = item({
            uploadedAt: '2024-01-20',
            takenAt: '2024-01-10',
        });
        const b = item({
            uploadedAt: '2024-01-15',
            takenAt: '2024-01-25',
        });
        // By uploaded, a is newer; by taken, b is newer.
        expect(compareGalleryItemsByDate(a, b, 'uploaded')).toBeLessThan(0);
        expect(compareGalleryItemsByDate(a, b, 'taken')).toBeGreaterThan(0);
    });

    test('never reorders folders', () => {
        const folder = {
            dataset: { itemType: 'folder', uploadedAt: '2020-01-01' },
            classList: { contains: () => false },
        };
        const photo = item({ uploadedAt: '2024-01-01' });
        expect(compareGalleryItemsByDate(folder, photo, 'uploaded')).toBe(0);
        expect(compareGalleryItemsByDate(photo, folder, 'uploaded')).toBe(0);
    });
});

describe('getColumnCountForWidth', () => {
    test('fits one column per MIN_COLUMN_WIDTH pixels', () => {
        expect(getColumnCountForWidth(MIN_COLUMN_WIDTH * 3)).toBe(3);
        expect(getColumnCountForWidth(MIN_COLUMN_WIDTH * 3 - 1)).toBe(2);
    });

    test('never drops below two columns', () => {
        expect(getColumnCountForWidth(100)).toBe(2);
        expect(getColumnCountForWidth(0)).toBe(2);
    });
});

describe('estimateGalleryItemHeight', () => {
    test('scales height by aspect ratio', () => {
        // 2:1 landscape in a 280px column -> 140px
        expect(estimateGalleryItemHeight(560, 280, 280)).toBe(140);
    });

    test('clamps extreme aspect ratios to [0.5, 2.0]', () => {
        // 10:1 panorama clamps to ratio 2.0 -> height 140
        expect(estimateGalleryItemHeight(2800, 280, 280)).toBe(140);
        // 1:10 tall strip clamps to ratio 0.5 -> height 560
        expect(estimateGalleryItemHeight(280, 2800, 280)).toBe(560);
    });

    test('returns null without dimensions (DOM fallback applies)', () => {
        expect(estimateGalleryItemHeight(0, 0, 280)).toBeNull();
        expect(estimateGalleryItemHeight(280, 0, 280)).toBeNull();
    });
});
