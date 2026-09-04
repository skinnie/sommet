/* Minimal iconv() for Windows/MinGW - just the subset openambit_libambit/utils.c needs.
 *
 * See iconv.h in this directory for why this exists. utils.c only ever converts TO "UTF-8",
 * from one of three source encodings:
 *   - NULL / "ASCII" (also any ISO-8859 / latin1 name): the watch's single-byte text fields.
 *     Each byte is treated as a Latin-1 code point (U+0000..U+00FF), which is exactly what the
 *     watch stores and never fails on bytes >= 0x80 (real iconv "ASCII" would error there;
 *     json_str() on the CLI side re-escapes anyway, so producing valid UTF-8 here is strictly
 *     safer than erroring the whole read).
 *   - "UTF-8": passed through byte-for-byte (already valid UTF-8).
 *   - "WCHAR_T": hidapi's wchar_t descriptor strings. wchar_t is 2 bytes on Windows, so the
 *     input is UTF-16LE; decoded here to code points (surrogate pairs handled) and re-emitted
 *     as UTF-8.
 *
 * Contract matches POSIX iconv closely enough for utils.c: advance the in and out buffer
 * pointers and their byte counts as bytes are consumed/produced, return 0 on success and
 * (size_t)-1 with errno==E2BIG if the output buffer fills. utils.c sizes output at n*4+1, so E2BIG cannot
 * happen for real inputs, but it is reported correctly regardless.
 */
#include "iconv.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { ENC_LATIN1, ENC_UTF8, ENC_WCHAR };

typedef struct { int from; } iconv_shim_t;

static int enc_of(const char *name) {
    if (!name)                      return ENC_LATIN1;   /* utils.c default is "ASCII" */
    if (!strcmp(name, "WCHAR_T"))   return ENC_WCHAR;
    if (!strcmp(name, "UTF-8") ||
        !strcmp(name, "UTF8"))      return ENC_UTF8;
    return ENC_LATIN1;                                   /* ASCII / ISO-8859-* / latin1 */
}

iconv_t iconv_open(const char *tocode, const char *fromcode) {
    (void)tocode;   /* utils.c always asks for UTF-8 output */
    iconv_shim_t *s = (iconv_shim_t *)malloc(sizeof(*s));
    if (!s) { errno = ENOMEM; return (iconv_t)-1; }
    s->from = enc_of(fromcode);
    return (iconv_t)s;
}

int iconv_close(iconv_t cd) {
    if (cd && cd != (iconv_t)-1) free(cd);
    return 0;
}

/* Append one Unicode code point to *outbuf as UTF-8; return -1 (no space) without consuming. */
static int put_utf8(uint32_t cp, char **outbuf, size_t *outbytesleft) {
    unsigned char *o = (unsigned char *)*outbuf;
    if (cp < 0x80) {
        if (*outbytesleft < 1) return -1;
        o[0] = (unsigned char)cp;
        *outbuf += 1; *outbytesleft -= 1;
    } else if (cp < 0x800) {
        if (*outbytesleft < 2) return -1;
        o[0] = (unsigned char)(0xC0 | (cp >> 6));
        o[1] = (unsigned char)(0x80 | (cp & 0x3F));
        *outbuf += 2; *outbytesleft -= 2;
    } else if (cp < 0x10000) {
        if (*outbytesleft < 3) return -1;
        o[0] = (unsigned char)(0xE0 | (cp >> 12));
        o[1] = (unsigned char)(0x80 | ((cp >> 6) & 0x3F));
        o[2] = (unsigned char)(0x80 | (cp & 0x3F));
        *outbuf += 3; *outbytesleft -= 3;
    } else {
        if (*outbytesleft < 4) return -1;
        o[0] = (unsigned char)(0xF0 | (cp >> 18));
        o[1] = (unsigned char)(0x80 | ((cp >> 12) & 0x3F));
        o[2] = (unsigned char)(0x80 | ((cp >> 6) & 0x3F));
        o[3] = (unsigned char)(0x80 | (cp & 0x3F));
        *outbuf += 4; *outbytesleft -= 4;
    }
    return 0;
}

size_t iconv(iconv_t cd, char **inbuf, size_t *inbytesleft,
             char **outbuf, size_t *outbytesleft) {
    iconv_shim_t *s = (iconv_shim_t *)cd;
    if (!s || s == (iconv_shim_t *)-1) { errno = EBADF; return (size_t)-1; }
    /* A flush call (inbuf == NULL) has nothing to do for these stateless conversions. */
    if (!inbuf || !*inbuf || !inbytesleft) return 0;

    if (s->from == ENC_WCHAR) {
        while (*inbytesleft >= 2) {
            uint16_t w;
            memcpy(&w, *inbuf, 2);
            uint32_t cp = w;
            if (w >= 0xD800 && w <= 0xDBFF && *inbytesleft >= 4) {   /* high surrogate */
                uint16_t w2;
                memcpy(&w2, *inbuf + 2, 2);
                if (w2 >= 0xDC00 && w2 <= 0xDFFF) {
                    cp = 0x10000u + (((uint32_t)(w - 0xD800)) << 10) + (uint32_t)(w2 - 0xDC00);
                    if (put_utf8(cp, outbuf, outbytesleft) < 0) { errno = E2BIG; return (size_t)-1; }
                    *inbuf += 4; *inbytesleft -= 4;
                    continue;
                }
            }
            if (put_utf8(cp, outbuf, outbytesleft) < 0) { errno = E2BIG; return (size_t)-1; }
            *inbuf += 2; *inbytesleft -= 2;
        }
        return 0;
    }

    while (*inbytesleft > 0) {
        unsigned char b = (unsigned char)**inbuf;
        if (s->from == ENC_UTF8) {              /* byte-for-byte passthrough */
            if (*outbytesleft < 1) { errno = E2BIG; return (size_t)-1; }
            **outbuf = (char)b;
            *outbuf += 1; *outbytesleft -= 1;
        } else {                                /* latin1: byte value IS the code point */
            if (put_utf8((uint32_t)b, outbuf, outbytesleft) < 0) { errno = E2BIG; return (size_t)-1; }
        }
        *inbuf += 1; *inbytesleft -= 1;
    }
    return 0;
}
