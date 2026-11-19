// The decoded Charts data kept in memory per move id (ActivityStreamsService), in its own module
// so Sommet Sync can drop a move whose track changed elsewhere without an import cycle (sync/08).
export const streamsCache = new Map<string, any>();
