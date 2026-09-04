/* Windows/MinGW has no <endian.h>: glibc's le16toh/htole32/... do not exist there, so every
 * vendored openambit_libambit source that byte-swaps fails to build ("implicit declaration of
 * function 'le32toh'"). Windows targets are little-endian; define the conversions with the
 * compiler's own byte-swap builtins, keyed on __BYTE_ORDER__, so the vendored sources stay
 * byte-identical to upstream openambit. Force-included from build.sh on Windows only (-include),
 * mirroring endian_compat_apple.h on macOS. */
#ifndef AMBIT_ENDIAN_COMPAT_WIN_H
#define AMBIT_ENDIAN_COMPAT_WIN_H
#if defined(_WIN32) && !defined(__APPLE__)
#include <stdint.h>
#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define htole16(x) ((uint16_t)(x))
#define le16toh(x) ((uint16_t)(x))
#define htole32(x) ((uint32_t)(x))
#define le32toh(x) ((uint32_t)(x))
#define htole64(x) ((uint64_t)(x))
#define le64toh(x) ((uint64_t)(x))
#define htobe16(x) __builtin_bswap16(x)
#define be16toh(x) __builtin_bswap16(x)
#define htobe32(x) __builtin_bswap32(x)
#define be32toh(x) __builtin_bswap32(x)
#define htobe64(x) __builtin_bswap64(x)
#define be64toh(x) __builtin_bswap64(x)
#else /* big-endian Windows is not a real target, but keep the mapping correct anyway */
#define htole16(x) __builtin_bswap16(x)
#define le16toh(x) __builtin_bswap16(x)
#define htole32(x) __builtin_bswap32(x)
#define le32toh(x) __builtin_bswap32(x)
#define htole64(x) __builtin_bswap64(x)
#define le64toh(x) __builtin_bswap64(x)
#define htobe16(x) ((uint16_t)(x))
#define be16toh(x) ((uint16_t)(x))
#define htobe32(x) ((uint32_t)(x))
#define be32toh(x) ((uint32_t)(x))
#define htobe64(x) ((uint64_t)(x))
#define be64toh(x) ((uint64_t)(x))
#endif
#endif
#endif
