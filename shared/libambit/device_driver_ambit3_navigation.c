/*
 * Serialization of the Suunto Ambit3 navigation database.
 * See the header for the scope and the verification references.
 */

#include "device_driver_ambit3_navigation.h"
#include "crc16.h"
#include "sha256.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Radius implied by the definition of the nautical mile, 1852 m per arc minute.
 * Determined by fitting on 1188 reference points, exact. This is not the 6367 km of
 * distance_calc(), which reproduces only 124/336 then 508/852 points. */
#define AMBIT3_ROUTE_RADIUS_M   (10800.0 * 1852.0 / M_PI)
/* Radius of calculateRelativeDistance() in SuuntoLink's route.js. */
#define AMBIT3_RELDIST_RADIUS_M 6378100.0

#define ROUTE_HEADER_LEN      32
#define ROUTE_DESC_LEN        52
#define ROUTE_POINT_LEN       12
#define ROUTE_INDEX_LEN       20
#define WAYPOINT_HEADER_LEN    6
#define WAYPOINT_INDEX_LEN     4

#define ROUTE_HEADER_MAGIC    0x340c  /* Ambit2: 0x3008 */
#define WAYPOINT_TAIL_MAGIC   0x0771
#define WAYPOINT_TYPE_DEFAULT 17      /* "Waypoint" on the Ambit enum side */
/* A Traverse / Traverse Alpha reads the type byte against its own 48-type list, where 17 is
 * "Hill" and the plain flag is 26 ("POI"). Twin of tools/build_route.py serialize(). */
#define WAYPOINT_TYPE_DEFAULT_TRAVERSE 26
#define ROUTE_INDEX_CONST     415

static void put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)((v >> 8) & 0xff);
    p[2] = (uint8_t)((v >> 16) & 0xff);
    p[3] = (uint8_t)((v >> 24) & 0xff);
}

/* 15 useful bytes then NUL, truncated on bytes and not on characters. */
static void put_name(uint8_t *dst, const char *name)
{
    size_t len = strlen(name);
    if (len > AMBIT3_MAX_NAME_BYTES) {
        len = AMBIT3_MAX_NAME_BYTES;
    }
    memset(dst, 0, 16);
    memcpy(dst, name, len);
}

/* Rounding of the magnitude: -2.5 gives -3, not -2. */
static int32_t round_magnitude(double value)
{
    int32_t magnitude = (int32_t)floor(fabs(value) + 0.5);
    return value < 0.0 ? -magnitude : magnitude;
}

static double to_radians(double degrees)
{
    return degrees * M_PI / 180.0;
}

void ambit3_nav_bbox_mid(const ambit3_nav_point_t *points, uint16_t count,
                         int32_t *mid_lat, int32_t *mid_lon)
{
    int32_t min_lat = points[0].latitude, max_lat = points[0].latitude;
    int32_t min_lon = points[0].longitude, max_lon = points[0].longitude;
    uint16_t i;

    for (i = 1; i < count; i++) {
        if (points[i].latitude < min_lat) min_lat = points[i].latitude;
        if (points[i].latitude > max_lat) max_lat = points[i].latitude;
        if (points[i].longitude < min_lon) min_lon = points[i].longitude;
        if (points[i].longitude > max_lon) max_lon = points[i].longitude;
    }
    /* Centre of the bbox, not the barycentre: max - (max-min)/2, as openambit does.
     * Computed in floating point to reproduce the reference's rounding. */
    *mid_lat = (int32_t)llround(max_lat - (max_lat - min_lat) / 2.0);
    *mid_lon = (int32_t)llround(max_lon - (max_lon - min_lon) / 2.0);
}

void ambit3_nav_relative_xy(int32_t mid_lat, int32_t mid_lon,
                            int32_t lat, int32_t lon, int32_t *x, int32_t *y)
{
    double mid_lat_deg = mid_lat / 1e7;
    double cos_mid = cos(to_radians(mid_lat_deg));

    *x = round_magnitude(AMBIT3_ROUTE_RADIUS_M * cos_mid
                         * to_radians((lon - mid_lon) / 1e7));
    *y = round_magnitude(AMBIT3_ROUTE_RADIUS_M
                         * to_radians((lat - mid_lat) / 1e7));
}

static double haversine_m(double lat_a, double lon_a, double lat_b, double lon_b)
{
    double la1 = to_radians(lat_a), lo1 = to_radians(lon_a);
    double la2 = to_radians(lat_b), lo2 = to_radians(lon_b);
    double dlat = sin((la2 - la1) / 2.0), dlon = sin((lo2 - lo1) / 2.0);
    double t = dlat * dlat + cos(la1) * cos(la2) * dlon * dlon;

    if (t > 1.0) {
        t = 1.0;
    }
    return 2.0 * AMBIT3_RELDIST_RADIUS_M * asin(sqrt(t));
}

/* Reproduces JavaScript's Number(x.toPrecision(4)). */
static double to_precision_4(double value)
{
    char buffer[32];
    snprintf(buffer, sizeof(buffer), "%.4g", value);
    return strtod(buffer, NULL);
}

/*
 * rel_distance: the travelled fraction, rounded to 4 significant digits the way
 * route.js does, scaled to 16 bits then truncated.
 */
static int relative_distances(const ambit3_nav_point_t *points, uint16_t count,
                              uint16_t *out)
{
    double *cumulative = calloc(count, sizeof(double));
    double total = 0.0;
    uint16_t i;

    if (cumulative == NULL) {
        return -1;
    }
    for (i = 1; i < count; i++) {
        total += haversine_m(points[i - 1].latitude / 1e7,
                             points[i - 1].longitude / 1e7,
                             points[i].latitude / 1e7,
                             points[i].longitude / 1e7);
        cumulative[i] = total;
    }
    for (i = 0; i < count; i++) {
        out[i] = total > 0.0
            ? (uint16_t)(to_precision_4(cumulative[i] / total) * 65535.0)
            : 0;
    }
    free(cumulative);
    return 0;
}

static void write_route_header(uint8_t *dst, uint16_t route_count,
                               uint32_t point_count, uint16_t checksum)
{
    memset(dst, 0, ROUTE_HEADER_LEN);
    put16(dst, ROUTE_HEADER_MAGIC);
    dst[2] = 0;
    dst[3] = 1;
    put16(dst + 4, route_count);
    put32(dst + 8, point_count);
    put16(dst + 12, checksum);
    put16(dst + 14, 1);  /* openambit puts 0 here for the Ambit2 */
}

static void write_route_descriptor(uint8_t *dst, const ambit3_nav_route_t *route,
                                   uint32_t start_index, int32_t mid_lat,
                                   int32_t mid_lon, int32_t max_x, int32_t max_y)
{
    memset(dst, 0, ROUTE_DESC_LEN);
    put_name(dst, route->name);
    put32(dst + 16, start_index);
    put16(dst + 20, route->point_count);
    put32(dst + 22, route->distance);
    put32(dst + 26, (uint32_t)mid_lat);
    put32(dst + 30, (uint32_t)mid_lon);
    put32(dst + 34, (uint32_t)max_x);
    put32(dst + 38, (uint32_t)max_y);
    put16(dst + 42, 0xffff);
    put16(dst + 44, 0xffff);
    put16(dst + 46, 0);
    put16(dst + 48, route->ascent);
    put16(dst + 50, route->descent);
}

static void write_waypoint_descriptor(uint8_t *dst,
                                      const ambit3_nav_waypoint_t *waypoint,
                                      const ambit3_nav_route_t *route, uint8_t rank,
                                      size_t len)
{
    memset(dst, 0, len);
    put32(dst, (uint32_t)waypoint->latitude);
    put32(dst + 4, (uint32_t)waypoint->longitude);
    if (len == AMBIT3_WAYPOINT_DESC_LEN_ALT) {
        /* Traverse: int16 altitude of the route point the waypoint sits on, 30000 = none. */
        int32_t altitude = AMBIT3_ALTITUDE_NONE;
        if (waypoint->point_index < route->point_count) {
            altitude = route->points[waypoint->point_index].altitude;
        }
        put16(dst + 8, (uint16_t)(int16_t)altitude);
        dst += 2;
    }
    put_name(dst + 8, waypoint->name);
    put_name(dst + 24, route->name);
    put16(dst + 40, WAYPOINT_TAIL_MAGIC);
    dst[42] = route->month;
    dst[43] = route->day;
    dst[44] = route->hour;
    dst[45] = route->minute;
    dst[46] = route->second;
    dst[47] = rank;
    dst[48] = (len == AMBIT3_WAYPOINT_DESC_LEN_ALT) ? WAYPOINT_TYPE_DEFAULT_TRAVERSE
                                                    : WAYPOINT_TYPE_DEFAULT;
}

static void add_write(ambit3_nav_plan_t *plan, uint32_t address,
                      const uint8_t *data, size_t length)
{
    size_t offset;

    for (offset = 0; offset < length; offset += AMBIT3_CHUNK_SIZE) {
        size_t chunk = length - offset;
        if (chunk > AMBIT3_CHUNK_SIZE) {
            chunk = AMBIT3_CHUNK_SIZE;
        }
        if (plan->write_count >= AMBIT3_NAV_MAX_WRITES) {
            return;
        }
        plan->writes[plan->write_count].address = (uint32_t)(address + offset);
        plan->writes[plan->write_count].length = (uint16_t)chunk;
        plan->writes[plan->write_count].data = data + offset;
        plan->write_count++;
    }
}

static void region_hash(const uint8_t *region, size_t size, char *out)
{
    uint8_t digest[32];
    int i;

    sha256(region, size, digest);
    for (i = 0; i < 32; i++) {
        sprintf(out + i * 2, "%02X", digest[i]);
    }
    out[64] = '\0';
}

static int alloc_regions(ambit3_nav_plan_t *plan)
{
    memset(plan, 0, sizeof(*plan));
    plan->waypoint_region = malloc(AMBIT3_WAYPOINT_REGION_SIZE);
    plan->route_region = malloc(AMBIT3_ROUTE_REGION_SIZE);
    if (plan->waypoint_region == NULL || plan->route_region == NULL) {
        ambit3_navigation_plan_free(plan);
        return -1;
    }
    /* Bytes never written are 0xff: the closing hash covers this complete region. */
    memset(plan->waypoint_region, 0xff, AMBIT3_WAYPOINT_REGION_SIZE);
    memset(plan->route_region, 0xff, AMBIT3_ROUTE_REGION_SIZE);
    return 0;
}

void ambit3_navigation_plan_free(ambit3_nav_plan_t *plan)
{
    if (plan == NULL) {
        return;
    }
    free(plan->waypoint_region);
    free(plan->route_region);
    plan->waypoint_region = NULL;
    plan->route_region = NULL;
    plan->write_count = 0;
}

size_t ambit3_waypoint_len_for_model(const char *model)
{
    if (model != NULL && (strcmp(model, "Jabiru") == 0 || strcmp(model, "Loon") == 0)) {
        return AMBIT3_WAYPOINT_DESC_LEN_ALT;
    }
    return AMBIT3_WAYPOINT_DESC_LEN;
}

static int plan_reset_sized(ambit3_nav_plan_t *plan, size_t waypoint_len);

int ambit3_navigation_plan_reset(ambit3_nav_plan_t *plan)
{
    return plan_reset_sized(plan, AMBIT3_WAYPOINT_DESC_LEN);
}

static int plan_reset_sized(ambit3_nav_plan_t *plan, size_t waypoint_len)
{
    uint8_t *route = NULL, *waypoint = NULL;

    if (alloc_regions(plan) != 0) {
        return -1;
    }
    route = plan->route_region;
    waypoint = plan->waypoint_region;

    /* Empty database: the CRC field and the word @14 are set to zero literally, not
     * computed. Observed on the routedelete and poiimport captures. */
    memset(route, 0, ROUTE_HEADER_LEN);
    put16(route, ROUTE_HEADER_MAGIC);
    route[3] = 1;
    memset(waypoint, 0, WAYPOINT_HEADER_LEN);
    put16(waypoint, (uint16_t)(0x300 + waypoint_len));
    put16(waypoint + 4, 0xffff);  /* crc16 of an empty table = the init value */

    add_write(plan, AMBIT3_WAYPOINT_BASE, waypoint, WAYPOINT_HEADER_LEN);
    region_hash(plan->waypoint_region, AMBIT3_WAYPOINT_REGION_SIZE,
                plan->waypoint_hash);
    add_write(plan, AMBIT3_ROUTE_BASE, route, ROUTE_HEADER_LEN);
    region_hash(plan->route_region, AMBIT3_ROUTE_REGION_SIZE, plan->route_hash);
    return 0;
}

int ambit3_navigation_plan(const ambit3_nav_route_t *routes, size_t route_count,
                           ambit3_nav_plan_t *plan)
{
    return ambit3_navigation_plan_sized(routes, route_count, AMBIT3_WAYPOINT_DESC_LEN, plan);
}

int ambit3_navigation_plan_sized(const ambit3_nav_route_t *routes, size_t route_count,
                                 size_t wpt_len, ambit3_nav_plan_t *plan)
{
    size_t i, total_points = 0, total_waypoints = 0;
    uint8_t *route_region, *waypoint_region;
    uint8_t *desc, *points, *index, *wpt_desc, *wpt_index;
    uint32_t point_cursor = 0;
    size_t waypoint_cursor;
    uint16_t *relative = NULL;
    int result = -1;

    if (route_count == 0) {
        return plan_reset_sized(plan, wpt_len);
    }
    if (route_count > AMBIT3_MAX_ROUTES
        || (wpt_len != AMBIT3_WAYPOINT_DESC_LEN
            && wpt_len != AMBIT3_WAYPOINT_DESC_LEN_ALT)) {
        return -1;
    }
    for (i = 0; i < route_count; i++) {
        if (routes[i].point_count > AMBIT3_MAX_ROUTE_POINTS) {
            return -1;
        }
        total_points += routes[i].point_count;
        total_waypoints += routes[i].waypoint_count;
    }
    if (total_points > AMBIT3_MAX_TOTAL_POINTS
        || total_waypoints > AMBIT3_MAX_WAYPOINTS) {
        return -1;
    }
    if (alloc_regions(plan) != 0) {
        return -1;
    }
    route_region = plan->route_region;
    waypoint_region = plan->waypoint_region;
    desc = route_region + (AMBIT3_ROUTE_DESC - AMBIT3_ROUTE_BASE);
    points = route_region + (AMBIT3_ROUTE_POINTS - AMBIT3_ROUTE_BASE);
    index = route_region + (AMBIT3_ROUTE_INDEX - AMBIT3_ROUTE_BASE);
    wpt_index = route_region + (AMBIT3_WAYPOINT_INDEX - AMBIT3_ROUTE_BASE);
    wpt_desc = waypoint_region + (AMBIT3_WAYPOINT_DESC - AMBIT3_WAYPOINT_BASE);

    memset(desc, 0, ROUTE_DESC_LEN * route_count);
    memset(points, 0, ROUTE_POINT_LEN * total_points);
    memset(index, 0, ROUTE_INDEX_LEN * route_count);
    memset(wpt_index, 0, WAYPOINT_INDEX_LEN * total_waypoints);
    memset(wpt_desc, 0, wpt_len * total_waypoints);

    /* SuuntoLink lays out the waypoint descriptor table in the REVERSE order of the
     * routes, whereas the index table follows their direct order. The two are
     * therefore not in correspondence as soon as there is more than one route.
     * Reproduced as is, to stay identical to the reference. */
    waypoint_cursor = total_waypoints;
    for (i = 0; i < route_count; i++) {
        const ambit3_nav_route_t *route = &routes[i];
        int32_t mid_lat, mid_lon, max_x = 0, max_y = 0;
        size_t block_start;
        uint16_t j;

        if (route->point_count == 0) {
            goto done;
        }
        relative = malloc(sizeof(uint16_t) * route->point_count);
        if (relative == NULL || relative_distances(route->points,
                                                   route->point_count,
                                                   relative) != 0) {
            goto done;
        }
        ambit3_nav_bbox_mid(route->points, route->point_count, &mid_lat, &mid_lon);
        for (j = 0; j < route->point_count; j++) {
            uint8_t *record = points + ROUTE_POINT_LEN * (point_cursor + j);
            int32_t x, y;
            int32_t altitude = route->points[j].altitude;

            ambit3_nav_relative_xy(mid_lat, mid_lon, route->points[j].latitude,
                                   route->points[j].longitude, &x, &y);
            if (j == 0 || x > max_x) max_x = x;
            if (j == 0 || y > max_y) max_y = y;
            put32(record, (uint32_t)x);
            put32(record + 4, (uint32_t)y);
            put16(record + 8, (uint16_t)altitude);
            put16(record + 10, relative[j]);
        }
        free(relative);
        relative = NULL;

        write_route_descriptor(desc + ROUTE_DESC_LEN * i, route,
                               point_cursor, mid_lat, mid_lon, max_x, max_y);

        /* This route's waypoint block, filled backwards from the end of the table. */
        waypoint_cursor -= route->waypoint_count;
        block_start = waypoint_cursor;
        for (j = 0; j < route->waypoint_count; j++) {
            write_waypoint_descriptor(wpt_desc + wpt_len * (block_start + j),
                                      &route->waypoints[j], route, (uint8_t)j,
                                      wpt_len);
        }

        put32(index + ROUTE_INDEX_LEN * i, (uint32_t)(i + 1));
        put32(index + ROUTE_INDEX_LEN * i + 4, route->timestamp);
        put32(index + ROUTE_INDEX_LEN * i + 8, ROUTE_INDEX_CONST);
        index[ROUTE_INDEX_LEN * i + 12] = (uint8_t)block_start;
        index[ROUTE_INDEX_LEN * i + 13] = (uint8_t)route->waypoint_count;
        index[ROUTE_INDEX_LEN * i + 15] =
            (uint8_t)(total_waypoints - block_start - route->waypoint_count);

        point_cursor += route->point_count;
    }

    /* Waypoint index, in the direct order of the routes. */
    {
        size_t slot = 0;
        for (i = 0; i < route_count; i++) {
            uint16_t j;
            for (j = 0; j < routes[i].waypoint_count; j++) {
                put32(wpt_index + WAYPOINT_INDEX_LEN * slot,
                      routes[i].waypoints[j].point_index);
                slot++;
            }
        }
    }

    put16(waypoint_region, (uint16_t)(0x300 + wpt_len));   /* 0x0334, or 0x0336 */
    put16(waypoint_region + 2, (uint16_t)total_waypoints);
    put16(waypoint_region + 4,
          crc16_ccitt_false(wpt_desc, wpt_len * total_waypoints));

    {
        /* The route header CRC covers the descriptors followed by the points, which
         * are not contiguous in the region. */
        size_t desc_len = ROUTE_DESC_LEN * route_count;
        size_t points_len = ROUTE_POINT_LEN * total_points;
        uint8_t *joined = malloc(desc_len + points_len);
        uint16_t checksum;

        if (joined == NULL) {
            goto done;
        }
        memcpy(joined, desc, desc_len);
        memcpy(joined + desc_len, points, points_len);
        checksum = crc16_ccitt_false(joined, desc_len + points_len);
        free(joined);
        write_route_header(route_region, (uint16_t)route_count,
                           (uint32_t)total_points, checksum);
    }

    /* SuuntoLink's emission order: the waypoint group then its closing, the route
     * group then its own. */
    add_write(plan, AMBIT3_WAYPOINT_BASE, waypoint_region, WAYPOINT_HEADER_LEN);
    add_write(plan, AMBIT3_WAYPOINT_DESC, wpt_desc,
              wpt_len * total_waypoints);
    region_hash(waypoint_region, AMBIT3_WAYPOINT_REGION_SIZE, plan->waypoint_hash);

    add_write(plan, AMBIT3_ROUTE_BASE, route_region, ROUTE_HEADER_LEN);
    add_write(plan, AMBIT3_ROUTE_DESC, desc, ROUTE_DESC_LEN * route_count);
    add_write(plan, AMBIT3_ROUTE_POINTS, points, ROUTE_POINT_LEN * total_points);
    add_write(plan, AMBIT3_ROUTE_INDEX, index, ROUTE_INDEX_LEN * route_count);
    add_write(plan, AMBIT3_WAYPOINT_INDEX, wpt_index,
              WAYPOINT_INDEX_LEN * total_waypoints);
    region_hash(route_region, AMBIT3_ROUTE_REGION_SIZE, plan->route_hash);

    result = 0;
done:
    free(relative);
    if (result != 0) {
        ambit3_navigation_plan_free(plan);
    }
    return result;
}
