import {
  listGear, createGear, updateGear, deleteGear,
  addReminder, updateReminder, deleteReminder, RemoteGear,
} from './ApiIntervalsIcuGear';
import {
  getAllGear, upsertGear, purgeGear, upsertReminder, getReminders, purgeReminder,
  getGearLedger, LocalGear, LocalReminder, newLocalId,
} from '../database/gearDb';
import { planGearSync, GearConflict, editableOf } from './GearDiff';
import { buildParentMap } from './GearRemoteModel';
import { computeGearTotals } from './GearTotals';

/** The gear's cumulative distance/time NOW (imported baseline + local additions) — the reset
 * baseline for a new/reset reminder so its local due-ness counts from this moment. */
async function gearTotalNow(gear: LocalGear): Promise<{ distanceM: number; timeS: number; activities: number }> {
  const ledger = await getGearLedger();
  const t = computeGearTotals(
    [{ id: gear.id, distanceM: gear.distanceM, timeS: gear.timeS, baselineAt: gear.lastSyncedAt }],
    ledger,
  ).get(gear.id)!;
  return { distanceM: t.distanceM, timeS: t.timeS, activities: t.addedCount };
}

// Two-way mirror between the local gear store and intervals.icu (decision 1).
//   1. pull remote + load local, reconcile via the pure planGearSync (GearDiff.ts)
//   2. apply every NON-conflicting action, then reconcile parent→child component_ids
//   3. return the conflicts untouched — the UI asks the user, then calls resolveConflict()
//
// Parent/child: intervals.icu links a parent to its parts via the parent's `component_ids`
// (no parentId on the child). Pulled gear adopts the remote id as its LOCAL id, so a local
// parent_id equals the parent's remote id — see localFromRemote / buildParentMap.
//
// Reminders: gear gets the full two-sided conflict engine; reminders are remote-authoritative
// for display (a pull refreshes the local copy, incl. the server's percent_used) while in-app
// edits write straight through (see the reminder helpers). One conflict engine, not two.

export interface MirrorResult {
  pulled: number;
  pushed: number;
  deleted: number;
  conflicts: GearConflict[];
}

function localFromRemote(r: RemoteGear, parentRemoteId: string | null, existing?: LocalGear): LocalGear {
  const now = Date.now();
  return {
    id: existing?.id ?? r.id,        // adopt the remote id as local id for freshly-pulled gear
    remoteId: r.id,
    parentId: parentRemoteId,        // equals the parent's local id (pulled gear: local id == remote id)
    name: r.name,
    type: r.type,
    distanceM: r.distanceM,
    timeS: r.timeS,
    retired: r.retired,
    isPrimary: existing?.isPrimary ?? false, // local-only flag, preserved across pulls
    updatedAt: now,
    lastSyncedAt: now,
    remoteSnapshot: JSON.stringify(r),
    deleted: false,
  };
}

async function syncRemindersFromRemote(gearLocalId: string, r: RemoteGear): Promise<void> {
  const existing = await getReminders(gearLocalId, true);
  for (const e of existing) await purgeReminder(e.id);
  for (const rr of r.reminders) {
    await upsertReminder({
      id: newLocalId('rem'), remoteId: rr.id, gearId: gearLocalId, name: rr.name,
      distanceM: rr.distanceM, timeS: rr.timeS, days: rr.days, activities: rr.activities,
      percentUsed: rr.percentUsed, snoozedUntil: rr.snoozedUntil,
      startingDistanceM: rr.startingDistanceM, startingTimeS: rr.startingTimeS,
      startingActivities: rr.startingActivities, lastReset: rr.lastReset,
      updatedAt: Date.now(), deleted: false,
    });
  }
}

function snapshotJson(l: LocalGear): string {
  return JSON.stringify({ id: l.remoteId, ...editableOf(l) });
}

/** Pull-only import: bring gear + components + reminders down from intervals.icu into the local
 * store, remote as the sole source of truth. NO pushes, NO deletes of local-only gear — safe to
 * run while still evaluating, and the primitive for eventually OWNING this data independently of
 * intervals.icu (André 2026-08-18: "get the info from intervals.icu… the aim is to ditch
 * intervals in the future"). On overlap remote simply wins; local-only gear is left untouched. */
export async function importFromIntervals(opts: { keepLocalEdits?: boolean } = {}): Promise<number> {
  const remotes = await listGear();
  const parentMap = buildParentMap(remotes);
  const locals = await getAllGear(true);
  // Match an existing local row by remote id so re-imports update in place (and keep the
  // local-only isPrimary flag) instead of duplicating.
  const byRemoteId = new Map<string, LocalGear>();
  for (const l of locals) if (l.remoteId) byRemoteId.set(l.remoteId, l);

  let imported = 0;
  for (const r of remotes) {
    const existing = byRemoteId.get(r.id);
    // The automatic refresh (keepLocalEdits) must not undo something changed here and not yet
    // sent - a rename, a retire (updatedAt later than lastSyncedAt). Such a row only takes the
    // new mileage; its name/retired/snapshot stay, and it stays marked as changed (updatedAt one
    // tick after lastSyncedAt) so a later Sync still pushes it. lastSyncedAt has to move: it is
    // also the moment GearTotals counts local rides from.
    if (opts.keepLocalEdits && existing && !existing.deleted && existing.updatedAt > existing.lastSyncedAt) {
      const now = Date.now();
      await upsertGear({ ...existing, distanceM: r.distanceM, timeS: r.timeS, lastSyncedAt: now, updatedAt: now + 1 });
      imported++;
      continue;
    }
    if (opts.keepLocalEdits && existing?.deleted) continue;   // deleted here, not yet sent: leave it
    await upsertGear(localFromRemote(r, parentMap.get(r.id) ?? null, existing));
    await syncRemindersFromRemote(existing?.id ?? r.id, r);
    imported++;
  }
  // No reconcileComponentLinks() here — that pushes; parent/child links already came from
  // parentMap. Import is strictly read-down.
  return imported;
}

export async function runGearMirror(): Promise<MirrorResult> {
  const remotes = await listGear();
  const locals = await getAllGear(true); // include tombstones
  const plan = planGearSync(locals, remotes);
  const parentMap = buildParentMap(remotes);

  const localById = new Map(locals.map(l => [l.id, l] as const));
  let pulled = 0, pushed = 0, deleted = 0;

  for (const a of plan.actions) {
   try {
    switch (a.kind) {
      case 'pull-create': {
        await upsertGear(localFromRemote(a.remote, parentMap.get(a.remote.id) ?? null));
        await syncRemindersFromRemote(a.remote.id, a.remote);
        pulled++;
        break;
      }
      case 'pull-update': {
        const existing = localById.get(a.localId);
        await upsertGear(localFromRemote(a.remote, parentMap.get(a.remote.id) ?? null, existing));
        await syncRemindersFromRemote(a.localId, a.remote);
        pulled++;
        break;
      }
      case 'push-create': {
        const l = localById.get(a.localId)!;
        const isComponent = l.parentId != null;
        const remoteId = await createGear({
          name: l.name, type: l.type, retired: l.retired, component: isComponent,
        });
        await upsertGear({ ...l, remoteId, lastSyncedAt: Date.now(),
          remoteSnapshot: JSON.stringify({ id: remoteId, ...editableOf(l) }) });
        pushed++;
        break;
      }
      case 'push-update': {
        const l = localById.get(a.localId)!;
        await updateGear(l.remoteId!, { name: l.name, type: l.type, retired: l.retired });
        await upsertGear({ ...l, lastSyncedAt: Date.now(), remoteSnapshot: snapshotJson(l) });
        pushed++;
        break;
      }
      case 'push-delete': {
        await deleteGear(a.remoteId);
        await purgeGear(a.localId);
        deleted++;
        break;
      }
      case 'delete-local': {
        await purgeGear(a.localId);
        deleted++;
        break;
      }
    }
   } catch (e: any) {
    // One bad row (e.g. a rejected gear type) must not sink the whole mirror.
    console.log('[gear] mirror action failed:', a.kind, e?.message ?? e);
   }
  }

  await reconcileComponentLinks().catch(e => console.log('[gear] component link reconcile failed:', e?.message ?? e));
  return { pulled, pushed, deleted, conflicts: plan.conflicts };
}

/** After creates/updates, make each remote parent's component_ids match its local children's
 * remote ids (a locally-added part only becomes a component once its parent lists it). */
async function reconcileComponentLinks(): Promise<void> {
  const locals = await getAllGear(); // non-deleted
  const byId = new Map(locals.map(l => [l.id, l] as const));
  const childrenByParent = new Map<string, LocalGear[]>();
  for (const l of locals) {
    if (l.parentId) {
      const arr = childrenByParent.get(l.parentId) ?? [];
      arr.push(l); childrenByParent.set(l.parentId, arr);
    }
  }
  for (const [parentLocalId, kids] of childrenByParent) {
    const parent = byId.get(parentLocalId);
    if (!parent?.remoteId) continue;
    const childRemoteIds = kids.map(k => k.remoteId).filter((x): x is string => !!x);
    let known: string[] = [];
    try { known = (JSON.parse(parent.remoteSnapshot || '{}').componentIds) ?? []; } catch { /* */ }
    const missing = childRemoteIds.some(id => !known.includes(id));
    if (childRemoteIds.length > 0 && missing) {
      await updateGear(parent.remoteId, { componentIds: childRemoteIds });
      await upsertGear({ ...parent,
        remoteSnapshot: JSON.stringify({ id: parent.remoteId, ...editableOf(parent), componentIds: childRemoteIds }) });
    }
  }
}

// ─── Conflict resolution (called once the user has chosen, per conflict) ───────

export type ConflictChoice = 'local' | 'remote';

export async function resolveConflict(conflict: GearConflict, choice: ConflictChoice): Promise<void> {
  const locals = await getAllGear(true);
  const l = locals.find(x => x.id === conflict.localId);
  if (!l) return;

  if (choice === 'local') {
    if (conflict.reason === 'remote-deleted-local-edited') {
      const remoteId = await createGear({ name: l.name, type: l.type, retired: l.retired, component: l.parentId != null });
      await upsertGear({ ...l, remoteId, lastSyncedAt: Date.now(), remoteSnapshot: JSON.stringify({ id: remoteId, ...editableOf(l) }), deleted: false });
    } else if (l.deleted) {
      if (l.remoteId) await deleteGear(l.remoteId);
      await purgeGear(l.id);
    } else if (l.remoteId) {
      await updateGear(l.remoteId, { name: l.name, type: l.type, retired: l.retired });
      await upsertGear({ ...l, lastSyncedAt: Date.now(), remoteSnapshot: snapshotJson(l) });
    }
  } else {
    if (conflict.reason === 'remote-deleted-local-edited') {
      await purgeGear(l.id);
    } else {
      const remotes = await listGear();
      const r = remotes.find(x => x.id === conflict.remoteId);
      if (r) {
        const parentMap = buildParentMap(remotes);
        await upsertGear(localFromRemote(r, parentMap.get(r.id) ?? null, l));
        await syncRemindersFromRemote(l.id, r);
      } else {
        await purgeGear(l.id);
      }
    }
  }
}

// ─── Reminder helpers (write-through to intervals.icu, local copy refreshed on pull) ──────

export type NewReminder = Pick<LocalReminder, 'name' | 'distanceM' | 'timeS' | 'days' | 'activities'>;

export async function createReminderNow(gear: LocalGear, r: NewReminder): Promise<void> {
  const localId = newLocalId('rem');
  let remoteId: string | null = null;
  if (gear.remoteId) remoteId = (await addReminder(gear.remoteId, r)) || null;
  const base = await gearTotalNow(gear); // count from the gear's current mileage
  await upsertReminder({
    id: localId, remoteId, gearId: gear.id, name: r.name,
    distanceM: r.distanceM, timeS: r.timeS, days: r.days, activities: r.activities,
    percentUsed: 0, snoozedUntil: null,
    startingDistanceM: base.distanceM, startingTimeS: base.timeS, startingActivities: base.activities,
    lastReset: Date.now(), updatedAt: Date.now(), deleted: false,
  });
}

export async function deleteReminderNow(gear: LocalGear, reminder: LocalReminder): Promise<void> {
  if (gear.remoteId && reminder.remoteId) {
    await deleteReminder(gear.remoteId, reminder.remoteId).catch(() => {});
  }
  await purgeReminder(reminder.id);
}

export async function snoozeReminderNow(gear: LocalGear, reminder: LocalReminder, days: number): Promise<void> {
  if (gear.remoteId && reminder.remoteId) {
    await updateReminder(gear.remoteId, reminder.remoteId, reminder, false, days).catch(() => {});
  }
  await upsertReminder({ ...reminder, snoozedUntil: Date.now() + days * 86400000, updatedAt: Date.now() });
}

/** Reset a reminder's counter (e.g. after servicing) — restart local due-ness from the gear's
 * current mileage and today, and push the reset to intervals. */
export async function resetReminderNow(gear: LocalGear, reminder: LocalReminder): Promise<void> {
  if (gear.remoteId && reminder.remoteId) {
    await updateReminder(gear.remoteId, reminder.remoteId, reminder, true, 0).catch(() => {});
  }
  const base = await gearTotalNow(gear);
  await upsertReminder({
    ...reminder, percentUsed: 0, snoozedUntil: null,
    startingDistanceM: base.distanceM, startingTimeS: base.timeS, startingActivities: base.activities,
    lastReset: Date.now(), updatedAt: Date.now(),
  });
}
