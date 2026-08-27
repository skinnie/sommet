/* macOS has no <endian.h>: glibc's le16toh/htole32/... simply do not exist there, so every
 * vendored openambit_libambit source that byte-swaps fails to build on a Mac ("call to
 * undeclared function 'le32toh'"). Apple ships the same primitives under different names in
 * <libkern/OSByteOrder.h>. Force-included from build.sh on Darwin only (-include), so the
 * vendored sources stay byte-identical to upstream openambit. */
#ifndef AMBIT_ENDIAN_COMPAT_APPLE_H
#define AMBIT_ENDIAN_COMPAT_APPLE_H
#if defined(__APPLE__)
#include <libkern/OSByteOrder.h>
#define htobe16(x) OSSwapHostToBigInt16(x)
#define htole16(x) OSSwapHostToLittleInt16(x)
#define be16toh(x) OSSwapBigToHostInt16(x)
#define le16toh(x) OSSwapLittleToHostInt16(x)
#define htobe32(x) OSSwapHostToBigInt32(x)
#define htole32(x) OSSwapHostToLittleInt32(x)
#define be32toh(x) OSSwapBigToHostInt32(x)
#define le32toh(x) OSSwapLittleToHostInt32(x)
#define htobe64(x) OSSwapHostToBigInt64(x)
#define htole64(x) OSSwapHostToLittleInt64(x)
#define be64toh(x) OSSwapBigToHostInt64(x)
#define le64toh(x) OSSwapLittleToHostInt64(x)
#endif
#endif
