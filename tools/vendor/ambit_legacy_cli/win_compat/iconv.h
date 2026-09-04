/* Minimal <iconv.h> shim for Windows/MinGW - see iconv_win.c for the implementation and the
 * reasoning. winlibs/MinGW-w64 ship the libiconv *runtime* DLL but no dev headers or import
 * lib, and this project's only iconv user (openambit_libambit/utils.c) converts just three
 * source encodings, all TO UTF-8: NULL/"ASCII" (the watch's ISO-8859 text fields), "UTF-8"
 * (already-UTF-8 activity names) and "WCHAR_T" (hidapi's wchar_t USB descriptor strings). This
 * header + iconv_win.c cover exactly that subset, so Windows builds need no external libiconv
 * at all - the vendored utils.c stays byte-identical to upstream (it just #include <iconv.h>s
 * this, picked up first via -I win_compat). Same "force a small Windows compat shim onto the
 * unmodified vendored sources" pattern as endian_compat_win.h in the parent directory. */
#ifndef AMBIT_ICONV_SHIM_WIN_H
#define AMBIT_ICONV_SHIM_WIN_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *iconv_t;

iconv_t iconv_open(const char *tocode, const char *fromcode);
size_t  iconv(iconv_t cd, char **inbuf, size_t *inbytesleft,
              char **outbuf, size_t *outbytesleft);
int     iconv_close(iconv_t cd);

#ifdef __cplusplus
}
#endif

#endif /* AMBIT_ICONV_SHIM_WIN_H */
