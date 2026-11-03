/**
 * Unit tests for the pure helper buildUploadJxlFields from upload.js.
 */
const { buildUploadJxlFields } = require('../../app/static/js/upload.js');

describe('buildUploadJxlFields', () => {
    test('defaults produce lossy q95/e7', () => {
        expect(buildUploadJxlFields(false, 95, 7)).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '95',
            jxl_effort: '7',
        });
    });

    test('lossless flag sets jxl_lossless to true', () => {
        expect(buildUploadJxlFields(true, 95, 7)).toEqual({
            jxl_lossless: 'true',
            jxl_quality: '95',
            jxl_effort: '7',
        });
    });

    test('string arguments are parseInt-coerced', () => {
        expect(buildUploadJxlFields(false, '80', '5')).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '80',
            jxl_effort: '5',
        });
    });

    test('NaN / undefined falls back to defaults 95/7', () => {
        expect(buildUploadJxlFields(false, undefined, undefined)).toEqual({
            jxl_lossless: 'false',
            jxl_quality: '95',
            jxl_effort: '7',
        });
    });
});
