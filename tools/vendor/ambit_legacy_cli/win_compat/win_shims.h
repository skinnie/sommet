/* Windows/MinGW build shims for the vendored openambit_libambit + ambit_legacy_cli sources.
 *
 * openambit builds on Linux, so a few of its sources reach for names MinGW's headers don't
 * provide. Rather than edit the vendored code, force-include this (build.sh's Windows branch,
 * -include) so those sources stay byte-identical to upstream - the same approach as
 * endian_compat_win.h next to it. Guarded to _WIN32 so a native Linux/macOS build is untouched.
 *
 *   - u_intN_t: BSD-style unsigned type names (sport_mode_serialize.c). MinGW defines the
 *     C99 uintN_t but not these.
 *   - setenv(): POSIX-only (ambit_legacy_cli.c's cmd_waypoints tightens the read timeout).
 *     Mapped onto the CRT's _putenv_s.
 */
#ifndef AMBIT_WIN_SHIMS_H
#define AMBIT_WIN_SHIMS_H

#if defined(_WIN32)
#include <stdint.h>
#include <stdlib.h>   /* _putenv_s */

typedef uint8_t  u_int8_t;
typedef uint16_t u_int16_t;
typedef uint32_t u_int32_t;
typedef uint64_t u_int64_t;

#ifndef setenv
static __inline int setenv(const char *name, const char *value, int overwrite) {
    (void)overwrite;   /* _putenv_s always overwrites, which is how the one caller uses it */
    return _putenv_s(name, value ? value : "");
}
#endif

#endif /* _WIN32 */
#endif /* AMBIT_WIN_SHIMS_H */
