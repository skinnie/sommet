// GENERATED from shared/sport_names.json by tools/gen_activity_view.py - edit that file.
#pragma once
#include <QHash>
#include <QSet>
#include <QString>

namespace SportNames {
inline const QHash<QString, QString> &intervals()
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("Ride"), QStringLiteral("Cycling")},
        {QStringLiteral("GravelRide"), QStringLiteral("Cycling")},
        {QStringLiteral("EBikeRide"), QStringLiteral("Cycling")},
        {QStringLiteral("CyclocrossRide"), QStringLiteral("Cycling")},
        {QStringLiteral("TrackRide"), QStringLiteral("Cycling")},
        {QStringLiteral("Handcycle"), QStringLiteral("Cycling")},
        {QStringLiteral("Velomobile"), QStringLiteral("Cycling")},
        {QStringLiteral("VirtualRide"), QStringLiteral("Indoor cycling")},
        {QStringLiteral("MountainBikeRide"), QStringLiteral("Mountain biking")},
        {QStringLiteral("EMountainBikeRide"), QStringLiteral("Mountain biking")},
        {QStringLiteral("Run"), QStringLiteral("Running")},
        {QStringLiteral("TrailRun"), QStringLiteral("Trail running")},
        {QStringLiteral("VirtualRun"), QStringLiteral("Treadmill")},
        {QStringLiteral("Walk"), QStringLiteral("Walking")},
        {QStringLiteral("Hike"), QStringLiteral("Hiking")},
        {QStringLiteral("Swim"), QStringLiteral("Pool swimming")},
        {QStringLiteral("OpenWaterSwim"), QStringLiteral("Openwater swimming")},
        {QStringLiteral("Rowing"), QStringLiteral("Indoor rowing")},
        {QStringLiteral("VirtualRow"), QStringLiteral("Indoor rowing")},
        {QStringLiteral("Kayaking"), QStringLiteral("Kayaking")},
        {QStringLiteral("Canoeing"), QStringLiteral("Canoeing")},
        {QStringLiteral("StandUpPaddling"), QStringLiteral("Standup paddling")},
        {QStringLiteral("WeightTraining"), QStringLiteral("Weight training")},
        {QStringLiteral("Workout"), QStringLiteral("Indoor training")},
        {QStringLiteral("HighIntensityIntervalTraining"), QStringLiteral("Circuit training")},
        {QStringLiteral("Crossfit"), QStringLiteral("Cross training")},
        {QStringLiteral("Elliptical"), QStringLiteral("Crosstrainer")},
        {QStringLiteral("StairStepper"), QStringLiteral("Indoor training")},
        {QStringLiteral("Yoga"), QStringLiteral("Yoga / pilates")},
        {QStringLiteral("Pilates"), QStringLiteral("Yoga / pilates")},
        {QStringLiteral("NordicSki"), QStringLiteral("Cross-country skiing")},
        {QStringLiteral("RollerSki"), QStringLiteral("Roller skiing")},
        {QStringLiteral("BackcountrySki"), QStringLiteral("Ski touring")},
        {QStringLiteral("AlpineSki"), QStringLiteral("Alpine skiing")},
        {QStringLiteral("Snowboard"), QStringLiteral("Snowboarding")},
        {QStringLiteral("Snowshoe"), QStringLiteral("Snow shoeing")},
        {QStringLiteral("IceSkate"), QStringLiteral("Ice skating")},
        {QStringLiteral("InlineSkate"), QStringLiteral("Roller skating")},
        {QStringLiteral("Golf"), QStringLiteral("Golf")},
        {QStringLiteral("Tennis"), QStringLiteral("Tennis")},
        {QStringLiteral("Badminton"), QStringLiteral("Badminton")},
        {QStringLiteral("TableTennis"), QStringLiteral("Table tennis")},
        {QStringLiteral("Squash"), QStringLiteral("Squash")},
        {QStringLiteral("Racquetball"), QStringLiteral("Racquet ball")},
        {QStringLiteral("Soccer"), QStringLiteral("Soccer / football")},
        {QStringLiteral("RockClimbing"), QStringLiteral("Climbing")},
        {QStringLiteral("Climbing"), QStringLiteral("Climbing")},
        {QStringLiteral("Surfing"), QStringLiteral("Surfing")},
        {QStringLiteral("Windsurf"), QStringLiteral("Windsurfing")},
        {QStringLiteral("Kitesurf"), QStringLiteral("Kitesurfing / kiting")},
        {QStringLiteral("Sail"), QStringLiteral("Sailing")},
        {QStringLiteral("Skateboard"), QStringLiteral("Unspecified sport")},
        {QStringLiteral("Other"), QStringLiteral("Unspecified sport")},
    };
    return map;
}

inline const QHash<QString, QString> &garmin()
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("running"), QStringLiteral("Running")},
        {QStringLiteral("street_running"), QStringLiteral("Running")},
        {QStringLiteral("track_running"), QStringLiteral("Running")},
        {QStringLiteral("trail_running"), QStringLiteral("Trail running")},
        {QStringLiteral("treadmill_running"), QStringLiteral("Treadmill")},
        {QStringLiteral("cycling"), QStringLiteral("Cycling")},
        {QStringLiteral("road_biking"), QStringLiteral("Cycling")},
        {QStringLiteral("gravel_cycling"), QStringLiteral("Cycling")},
        {QStringLiteral("e_bike_fitness"), QStringLiteral("Cycling")},
        {QStringLiteral("mountain_biking"), QStringLiteral("Mountain biking")},
        {QStringLiteral("e_bike_mountain"), QStringLiteral("Mountain biking")},
        {QStringLiteral("indoor_cycling"), QStringLiteral("Indoor cycling")},
        {QStringLiteral("virtual_ride"), QStringLiteral("Indoor cycling")},
        {QStringLiteral("walking"), QStringLiteral("Walking")},
        {QStringLiteral("hiking"), QStringLiteral("Hiking")},
        {QStringLiteral("lap_swimming"), QStringLiteral("Pool swimming")},
        {QStringLiteral("open_water_swimming"), QStringLiteral("Openwater swimming")},
        {QStringLiteral("strength_training"), QStringLiteral("Weight training")},
        {QStringLiteral("fitness_equipment"), QStringLiteral("Indoor training")},
        {QStringLiteral("indoor_cardio"), QStringLiteral("Indoor training")},
        {QStringLiteral("hiit"), QStringLiteral("Circuit training")},
        {QStringLiteral("yoga"), QStringLiteral("Yoga / pilates")},
        {QStringLiteral("pilates"), QStringLiteral("Yoga / pilates")},
        {QStringLiteral("elliptical"), QStringLiteral("Crosstrainer")},
        {QStringLiteral("indoor_rowing"), QStringLiteral("Indoor rowing")},
        {QStringLiteral("mountaineering"), QStringLiteral("Mountaineering")},
        {QStringLiteral("resort_skiing_snowboarding"), QStringLiteral("Alpine skiing")},
        {QStringLiteral("cross_country_skiing"), QStringLiteral("Cross-country skiing")},
        {QStringLiteral("backcountry_skiing"), QStringLiteral("Ski touring")},
    };
    return map;
}

inline const QHash<QString, QString> &aliases()
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("Trail Running"), QStringLiteral("Trail running")},
        {QStringLiteral("Swimming"), QStringLiteral("Pool swimming")},
        {QStringLiteral("Openwater swim"), QStringLiteral("Openwater swimming")},
        {QStringLiteral("Gym training"), QStringLiteral("Weight training")},
        {QStringLiteral("Cross country skiing"), QStringLiteral("Cross-country skiing")},
        {QStringLiteral("Other"), QStringLiteral("Unspecified sport")},
    };
    return map;
}

inline const QSet<QString> &foot()
{
    static const QSet<QString> set = {QStringLiteral("Running"), QStringLiteral("Trail running"), QStringLiteral("Treadmill"), QStringLiteral("Walking"), QStringLiteral("Nordic walking"), QStringLiteral("Hiking"), QStringLiteral("Trekking"), QStringLiteral("Orienteering"), QStringLiteral("Mountaineering"), QStringLiteral("Snow shoeing"), QStringLiteral("Track and field")};
    return set;
}
}  // namespace SportNames
