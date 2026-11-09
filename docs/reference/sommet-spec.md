# Sommet

> Saved here 2026-08-06 as a reference/context document. This describes a separate Qt 6 /
> QML / C++ desktop application ("Sommet") with its own existing codebase, distinct from
> the Python reverse-engineering tooling in the rest of this repo (`tools/`) and from the
> React Native `opensportsync` fork referenced in `HANDOFF.md` as the mobile base app. That
> Qt/QML codebase itself is not present in this repo/session - this file is the spec only,
> kept for context if/when that project's work intersects with this one (e.g. the open
> Sport Modes questions in `unresolved_questions_for_devs.md`, or the CustomModes/format
> findings in `custom_modes_andre.md`).

## Mission

Sommet is the modern companion application for legacy Suunto Ambit watches.

Its purpose is to preserve and improve the Ambit experience after the official Suunto ecosystem.

Sommet is NOT trying to become:

- Garmin Connect
- Suunto App
- Intervals.icu
- Strava

Those platforms already exist.

Sommet focuses on the watch.

Everything starts with the watch.

---

# Core Principles

Every feature should respect these principles.

Simple

Reliable

Outdoor

If a feature does not improve one of those three, it probably does not belong in Sommet.

---

# Current Project

IMPORTANT

Do NOT rewrite the application.

The current codebase already contains a large amount of working functionality.

Always inspect the existing implementation before creating new code.

Reuse existing code whenever possible.

Refactor only when necessary.

The goal is a modern UI and architecture, not rewriting the backend.

---

# Existing Features

Already implemented

✓ Activity synchronization

✓ Route management

- Import GPX
- Export GPX
- Upload
- Download

✓ POI management

- Coordinates
- GPX Import
- GPX Export
- Upload
- Download

✓ Third-party integrations

- Intervals.icu
- Runalyze
- Strava

These features already work.

Improve the interface.

Do not replace them.

---

# Future Features

Not implemented yet

Sport Modes

Keep the architecture ready.

Hide the navigation entry using a feature flag.

Example

FeatureFlags::SportModes = false

Once enabled the navigation automatically appears.

No redesign required later.

---

# Technology

Qt 6

Qt Quick Controls 2

QML

Backend

C++

Existing backend remains.

QML must NEVER directly communicate with libambit.

Architecture

QML

↓

ViewModels

↓

Services

↓

Current Backend

↓

libambit

---

# Project Structure

Theme.qml

DeviceService

ActivityService

RouteService

PoiService

MapService

WeatherService

SyncService

SettingsService

FeatureFlags

DeviceCapabilities

---

# Device Capabilities

Never hardcode watch models.

Instead expose capabilities.

Example

supportsRoutes

supportsPOIs

supportsSportModes

supportsApps

supportsNavigation

supportsBluetooth

supportsFirmware

The UI should automatically adapt.

This will allow future support for

Ambit1

Ambit2

Ambit3

Traverse

without redesign.

---

# Theme

Create a reusable theme system.

Never hardcode colors.

Theme.qml

Background

Card

Primary

Secondary

Accent

Success

Warning

Error

Text

MutedText

Support

Light

Dark

Future themes should require zero UI changes.

---

# Icons

Use Material Symbols Rounded.

No emoji.

No platform-specific icons.

---

# Localization

Every visible string must use

qsTr()

No hardcoded English strings.

Prepare for future translations.

English

French

Portuguese

---

# Navigation

Home

Activities

Routes

POIs

Backup

Settings

Sport Modes (hidden)

---

# HOME

The watch is the hero.

Only show ONE watch.

Do not repeat the watch elsewhere.

Use the official Ambit 3 Peak Sapphire images already available inside the Suunto Android resources.

Device card

Watch Image

Watch Name

Connection Status

Battery

Firmware

GPS Orbit validity

Future information may be added without redesigning the card.

Obtain device information from the current backend.

Inspect OpenAmbit/libambit if additional fields are needed.

---

# Last Activity

Large map preview

Distance

Duration

Elevation

Button

View Activities

---

# Maps

Use MapLibre.

Create a MapService abstraction.

Support

Online

OpenStreetMap

Offline

MBTiles (future)

The UI must never care where tiles come from.

---

# Weather

Provider

Open-Meteo

Reason

No API key

Simple REST API

Excellent European forecasts

Weather card

Current temperature

Current conditions

Wind

Today's High

Today's Low

Three-day forecast

If weather retrieval fails

Hide the card.

No popup.

No error.

Location

Initially

User configured location.

Future

Watch last GPS position.

WeatherService should allow changing the location source without UI modifications.

---

# New Activities

Show activities waiting for upload.

Each row

Sport icon

Date

Distance

Name

Button

Sync

---

# Connections

Display only status.

Intervals.icu

Runalyze

Strava

Green

Connected

Grey

Disconnected

The entire card should open

Settings → Connections

Do not place buttons on the dashboard.

---

# Activities

Already implemented.

Redesign only.

Think Apple Photos.

Large cards.

Small map preview.

Sport icon.

Distance.

Duration.

Elevation.

Selecting an activity opens

Large MapLibre map

Overview

Charts

Laps

Export

Upload

Notes

---

# Routes

Already implemented.

Improve only the interface.

Future

Drag & Drop GPX

Thumbnail maps

Search

---

# POIs

Already implemented.

Improve only the interface.

Add

Search

Small preview map

---

# Backup

Rename

Backup & Restore

Initially support

Routes

POIs

Export

Restore

Future

Sport Modes

Settings

Profiles

---

# Settings

General

Connections

Maps

Weather

Backup

About

Connections

Intervals.icu

Runalyze

Strava

Maps

MapLibre

Offline MBTiles (future)

Weather

Open-Meteo

Enable

Refresh interval

Manual location

Automatic location

---

# Logging

Every synchronization should produce a log.

Example

Watch Connected

Downloaded 3 activities

Uploaded to Intervals

Uploaded to Runalyze

Finished Successfully

This log should be accessible from the UI.

---

# Search

Activities

Routes

POIs

All should support search.

Implement a reusable search component.

---

# Design Language

Modern

Minimal

Outdoor

Native

Fast

Large whitespace

Rounded cards

Subtle shadows

Subtle animations

No ribbons

No tree widgets

No Windows XP feeling

No dashboard full of charts.

---

# Explicitly DO NOT Implement

Training analytics

FTP

VO2max

Recovery

Storage usage

Software update panels

Cloud synchronization providers

Dropbox

Google Drive

OneDrive

MEGA

Synology Drive

Komoot

Quantified Self

Those belong to future versions.

The objective of V2 is a polished desktop experience.

---

# Implementation Strategy

Do NOT generate the whole UI at once.

Work incrementally.

Step 1

Theme.qml

Step 2

Reusable Card component

Step 3

Navigation

Step 4

Home page

Step 5

Weather

Step 6

MapLibre component

Step 7

Activity cards

Step 8

Routes redesign

Step 9

POIs redesign

Step 10

Backup page

Step 11

Settings
