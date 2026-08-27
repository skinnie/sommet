cmake_minimum_required(VERSION 3.21)
project(AmbitApp VERSION 0.2.3 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 QuickDialogs2
    Network Sql)

# Step 6 (Maps) - two real backends tried and abandoned, both after real hardware testing:
# maplibre-native-qt (a separate ~1.7GB build) crashed outright (std::bad_alloc, reproducible
# even under forced software OpenGL, so not a driver quirk); Qt's own built-in "osm"
# GeoServices plugin kept showing a "missing API key" watermark that its documented
# parameters couldn't clear (this Qt 6.12 build's plugin binary has none of the documented
# "osm.mapping.*" parameter strings at all - checked directly with `strings`, not guessed a
# third time). MapView.qml is now a plain direct XYZ tile renderer instead - no
# GeoServices plugin, no Qt6::Location/Qt6::Positioning dependency at all. See MapView.qml
# and V3_CHANGELOG.md, 2026-08-07.

qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(ambitapp
    src/main.cpp
)

# The QML module URI is "AmbitApp" (below), which Qt materialises as an "AmbitApp" directory
# in the build tree. On a case-INSENSITIVE filesystem (macOS APFS, Windows) a target output
# also named "ambitapp" collides with that directory - the macOS linker fails with
# "ld: open() failed, errno=21 (Is a directory) for 'ambitapp'". Naming the OUTPUT "Sommet"
# both dodges the collision and is the correct user-facing brand (the internal target/module
# names stay ambitapp/AmbitApp so nothing else in this file changes). MACOSX_BUNDLE makes the
# macOS output a real Sommet.app (the release .dmg wraps it); WIN32_EXECUTABLE drops the
# console window on the Windows GUI build. All three are no-ops on Linux except the rename.
set_target_properties(ambitapp PROPERTIES
    OUTPUT_NAME Sommet
    MACOSX_BUNDLE TRUE
    WIN32_EXECUTABLE TRUE
    # Without these, Qt stamps the macOS Info.plist with its placeholders: CFBundleIdentifier
    # com.yourcompany.ambitapp and CFBundleName ambitapp (the old internal target name). The app
    # already brands itself Sommet everywhere else and writes QSettings under com.sommet.Sommet,
    # so align the bundle identity to match (spotted on the first Mac build, 2026-08-27).
    MACOSX_BUNDLE_GUI_IDENTIFIER com.sommet.Sommet
    MACOSX_BUNDLE_BUNDLE_NAME Sommet
    # Qt defaults CFBundleShortVersionString to major.minor only ("0.2"), so 0.2.0/0.2.1/0.2.2
    # all showed as "0.2" in Finder Get Info and the About box - users couldn't tell builds
    # apart when reporting bugs. Stamp the full project version into both version keys.
    MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION}
    MACOSX_BUNDLE_BUNDLE_VERSION ${PROJECT_VERSION}
)

# Pure-QML singletons - see qml/Theme.qml's own header comment for why these are QML, not
# C++ types: no native state or backend calls belong in any of them. MUST be set before
# qt_add_qml_module() below, not after: it reads this property while generating qmldir, so
# setting it later writes every one of these as a plain type instead of `singleton NAME ...`
# - real bug, found 2026-08-07 on the first real build (every Theme/Icons property came back
# undefined at runtime, see V3_CHANGELOG.md for the full symptom and how it was traced).
set_source_files_properties(qml/Theme.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/FeatureFlags.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/DeviceCapabilities.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/Icons.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/ActivityTypes.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/WatchUnits.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/ActivityMetrics.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/TotalsFacts.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/NavBus.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/SunTimes.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/FunFacts.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/viewmodels/HomeViewModel.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/viewmodels/WeatherViewModel.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/MapService.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/viewmodels/ActivityViewModel.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)
set_source_files_properties(qml/viewmodels/RouteViewModel.qml PROPERTIES
    QT_QML_SINGLETON_TYPE true
)

qt_add_qml_module(ambitapp
    URI AmbitApp
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/Theme.qml
        qml/FeatureFlags.qml
        qml/DeviceCapabilities.qml
        qml/Icons.qml
        qml/ActivityTypes.qml
        qml/components/Card.qml
        qml/components/SommetMark.qml
        qml/components/PageFlickable.qml
        qml/components/ErrorBanner.qml
        qml/components/MapWindow.qml
        qml/components/LayoutPickerDialog.qml
        qml/components/CreateSportModeDialog.qml
        qml/components/MultisportEditorDialog.qml
        qml/components/ThemedDialog.qml
        qml/components/ClockSyncDialog.qml
        qml/components/ActivityRow.qml
        qml/components/DemoDevicePicker.qml
        qml/components/LoadingPill.qml
        qml/WatchUnits.qml
        qml/ActivityMetrics.qml
        qml/TotalsFacts.qml
        qml/NavBus.qml
        qml/SunTimes.qml
        qml/FunFacts.qml
        qml/components/TotalsCard.qml
        qml/components/HomeLocationDialog.qml
        qml/components/PlaceSearchBar.qml
        qml/pages/TotalsPage.qml
        qml/pages/CalendarPage.qml
        qml/pages/GearPage.qml
        qml/pages/CoachPage.qml
        qml/components/Icon.qml
        qml/components/RoundedComboBox.qml
        qml/components/ThemedMenu.qml
        qml/components/ThemedMenuItem.qml
        qml/components/ViewModeToggle.qml
        qml/components/RoundedButton.qml
        qml/components/RoundedSlider.qml
        qml/components/RoundedTextField.qml
        qml/components/RoundedCheckBox.qml
        qml/components/RoundedRadioButton.qml
        qml/components/RoundedSwitch.qml
        qml/components/ActivityBadge.qml
        qml/components/WatchFacePreview.qml
        qml/components/DataPickerDialog.qml
        qml/components/NavItem.qml
        qml/components/NavRail.qml
        qml/components/PagePlaceholder.qml
        qml/components/WeatherCard.qml
        qml/components/MapView.qml
        qml/components/ActivityCard.qml
        qml/components/ActivityDetail.qml
        qml/components/RuleOutputChart.qml
        qml/components/EtrexIcon.qml
        qml/components/IntervalsIcon.qml
        qml/MapService.qml
        qml/viewmodels/HomeViewModel.qml
        qml/viewmodels/WeatherViewModel.qml
        qml/viewmodels/ActivityViewModel.qml
        qml/viewmodels/RouteViewModel.qml
        qml/pages/HomePage.qml
        qml/pages/ActivitiesPage.qml
        qml/pages/RoutesPage.qml
        qml/pages/PoisPage.qml
        qml/pages/BackupPage.qml
        qml/pages/FirmwarePage.qml
        qml/pages/WatchSettingsPage.qml
        qml/pages/SmartSensorPage.qml
        qml/pages/SettingsPage.qml
        qml/pages/SportModesPage.qml
        qml/pages/IntervalsPage.qml
        qml/pages/WeightPage.qml
        qml/pages/HealthPage.qml
        qml/pages/EmberPage.qml
        qml/components/EmberBars.qml
        qml/components/MetricChart.qml
        qml/pages/AppZonePage.qml
        qml/pages/TrainingProgramPage.qml
        qml/pages/GpsTrackPodPage.qml
        qml/pages/SuuntoT6Page.qml
    # Step 10+ pages/components get added here as they're built - each spec step should
    # compile independently, so this list grows incrementally rather than all at once.
    SOURCES
        src/services/backendprocess.h
        src/services/backendprocess.cpp
        src/services/deviceservice.h
        src/services/deviceservice.cpp
        src/services/logservice.h
        src/services/logservice.cpp
        src/services/weatherservice.h
        src/services/weatherservice.cpp
        src/services/activityservice.h
        src/services/activityservice.cpp
        src/services/gearservice.h
        src/services/gearservice.cpp
        src/services/coachservice.h
        src/services/coachservice.cpp
        src/services/routeservice.h
        src/services/routeservice.cpp
        src/services/poiservice.h
        src/services/poiservice.cpp
        src/services/backupservice.h
        src/services/backupservice.cpp
        src/services/connectionsservice.h
        src/services/connectionsservice.cpp
        src/services/localfileservice.h
        src/services/localfileservice.cpp
        src/services/garminservice.h
        src/services/garminservice.cpp
        src/services/weightservice.h
        src/services/weightservice.cpp
        src/services/healthservice.h
        src/services/healthservice.cpp
        src/services/intervalsservice.h
        src/services/intervalsservice.cpp
        src/services/appzoneservice.h
        src/services/appzoneservice.cpp
        src/services/kailashservice.h
        src/services/kailashservice.cpp
        src/services/settingswriteservice.h
        src/services/settingswriteservice.cpp
        src/services/customodesservice.h
        src/services/customodesservice.cpp
        src/services/appsservice.h
        src/services/appsservice.cpp
        src/services/tilecachepaths.h
        src/services/tilecacheservice.h
        src/services/tilecacheservice.cpp
        src/services/smartsensorservice.h
        src/services/smartsensorservice.cpp
        src/services/trainingprogramservice.h
        src/services/trainingprogramservice.cpp
    RESOURCES
        assets/fonts/MaterialSymbolsRounded.ttf
        packaging/icon.png
        # Coach's bundled catalogue (2026-08-21) - a copy of coach/data/systm-sample.json.
        # coach/ itself is gitignored / not part of this public repo (its own README says
        # so explicitly), so CoachService reads its own tracked copy here, not that path.
        assets/coach/systm-sample.json
        # assets/map/osm-raster-style.json was MapLibre's own style file - unused since the
        # 2026-08-07 switch to Qt's built-in "osm" plugin, left on disk but out of the build
)

target_link_libraries(ambitapp PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Network
    Qt6::Sql
)

# qt_add_qml_module's auto-generated type-registration file (ambitapp_qmltyperegistrations.cpp)
# looks up each QML_ELEMENT header by bare filename (__has_include(<deviceservice.h>)), not by
# its path under src/services/ - needs that directory on the include path explicitly, or every
# one of those checks silently fails and the include gets skipped (2026-08-07, first real build).
target_include_directories(ambitapp PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src/services
)

# IntervalsService's own repo-root path for launching tools/workout_gui.py / dist/<os>/ - see
# that Service's own header comment for why this is baked in at configure time rather than
# guessed at runtime.
target_compile_definitions(ambitapp PRIVATE
    AMBITAPP_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
)
