/**
 * Unit tests for the pure helper buildUploadJxlFields from upload.js.
 *
 * Quality 100 means lossless; anything below is lossy at that quality.
 */
const { buildUploadJxlFields } = require('../../app/static/js/upload.js');

describe('buildUploadJxlFields', () => {
    test('defaults produce lossy q95/e7', () => {
        expect(buildUploadJxlFields(95, 7)).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '95',
            jxl_effort: '7',
        });
    });

    test('quality 100 switches to lossless', () => {
        expect(buildUploadJxlFields(100, 9)).toEqual({
            jxl_lossless: 'true',
            jxl_quality: '100',
            jxl_effort: '9',
        });
    });

    test('string arguments are parseInt-coerced', () => {
        expect(buildUploadJxlFields('80', '5')).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '80',
            jxl_effort: '5',
        });
    });

    test('NaN / undefined falls back to defaults 95/7', () => {
        expect(buildUploadJxlFields(undefined, undefined)).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '95',
            jxl_effort: '7',
        });
    });
});
