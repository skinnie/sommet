# - Resolve what hidapi driver to use
# This module is affected by the following defines
#  HIDAPI_DRIVER (possible values: libudev, libusb, pcapsimulate, mac, windows)
#
# This module defines
#  HIDAPI_INCLUDE_DIR
#  HIDAPI_SOURCE_FILES
#  HIDAPI_LIBS

if (NOT HIDAPI_RESOLVED)
    if (HIDAPI_DRIVER STREQUAL "system")
        # Link the platform's own modern hidapi instead of the sources vendored here.
        # The bundled hidapi is a ~2010 snapshot: on macOS its hid-mac.c enumerates but
        # then hangs on open against current IOKit, and its hid-libusb.c does not even
        # compile (pthread_barrier_* is unimplemented on Darwin). The system hidapi is
        # the same library the Python side already talks to this hardware through.
        find_package(PkgConfig REQUIRED)
        pkg_check_modules(SYSTEM_HIDAPI REQUIRED hidapi)
        set (HIDAPI_INCLUDE_DIR ${SYSTEM_HIDAPI_INCLUDE_DIRS})
        set (HIDAPI_SOURCE_FILES "")
        set (HIDAPI_LIBS ${SYSTEM_HIDAPI_LINK_LIBRARIES})
        if (APPLE)
            find_library(ICONV_LIBRARY iconv REQUIRED)   # libambit's own utils.c needs it
            list (APPEND HIDAPI_LIBS ${ICONV_LIBRARY})
        endif (APPLE)
    elseif (HIDAPI_DRIVER STREQUAL "libusb")
        find_package(libusb REQUIRED)
        set (HIDAPI_INCLUDE_DIR "hidapi" ${LIBUSB_INCLUDE_DIR})
        set (HIDAPI_SOURCE_FILES "hidapi/hid-libusb.c")
        set (HIDAPI_LIBS ${LIBUSB_LIBRARIES})
    elseif (HIDAPI_DRIVER STREQUAL "pcapsimulate")
        find_package(PCAP REQUIRED)
        set (HIDAPI_INCLUDE_DIR "hidapi" ${PCAP_INCLUDE_DIR})
        set (HIDAPI_SOURCE_FILES "hidapi/hid-pcapsimulate.c")
        set (HIDAPI_LIBS ${PCAP_LIBRARY})
    elseif (HIDAPI_DRIVER STREQUAL "mac")
# hid-mac.c talks to IOHIDManager, so it needs IOKit and CoreFoundation. HIDAPI_LIBS was
# left empty here ("Mac is still untested"), which compiles fine and then fails to link
# with undefined _CFGetTypeID/_IOHIDManager* - the macOS build never got this far before.
        set (HIDAPI_INCLUDE_DIR "hidapi" "")
        set (HIDAPI_SOURCE_FILES "hidapi/hid-mac.c")
        find_library(IOKIT_LIBRARY IOKit REQUIRED)
        find_library(COREFOUNDATION_LIBRARY CoreFoundation REQUIRED)
        find_library(ICONV_LIBRARY iconv REQUIRED)   # hid-mac.c converts the UTF-32 IOKit strings
        set (HIDAPI_LIBS ${IOKIT_LIBRARY} ${COREFOUNDATION_LIBRARY} ${ICONV_LIBRARY})
    elseif (HIDAPI_DRIVER STREQUAL "windows")
        find_package(iconv REQUIRED)
        set (HIDAPI_INCLUDE_DIR "hidapi" ${ICONV_INCLUDE_DIR})
        set (HIDAPI_SOURCE_FILES "hidapi/hid-windows.c")
        set (HIDAPI_LIBS ${ICONV_LIBRARY} setupapi)
    else (HIDAPI_DRIVER STREQUAL "libusb")
        find_package(UDev REQUIRED)
        set (HIDAPI_INCLUDE_DIR "hidapi" ${UDEV_INCLUDE_DIR})
        set (HIDAPI_SOURCE_FILES "hidapi/hid-linux.c")
        set (HIDAPI_LIBS ${UDEV_LIBS})
    endif (HIDAPI_DRIVER STREQUAL "libusb")

    mark_as_advanced(HIDAPI_INCLUDE_DIR HIDAPI_SOURCE_FILES HIDAPI_LIBS)
    set (HIDAPI_RESOLVED TRUE)
    message(STATUS "Found hidapi: ${HIDAPI_DRIVER} ${HIDAPI_INCLUDE_DIR} ${HIDAPI_SOURCE_FILES} ${HIDAPI_LIBS}")
endif (NOT HIDAPI_RESOLVED)
