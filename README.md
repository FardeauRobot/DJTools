# DJTools

Browse, play, tag and organise a DJ library; tags are written into rekordbox as real **My Tags**.

```sh
./run.sh                      # start the app
```

No setup step: `run.sh` creates `.venv` on the first run (any Python 3.10+ on `PATH`) and reinstalls
whenever `requirements.txt` changes.

## rekordbox is optional

**Library → Sync tags with rekordbox** turns the whole rekordbox side on or off. The first run decides by
looking for `~/Library/Pioneer/rekordbox/master.db`, and the setting is remembered from then on.

**Off**, DJTools never opens rekordbox's database. Browsing, playing, search, the BPM and key filters,
links, Clean up names and Library health all work; BPM and key come from the file tags instead of
rekordbox's analysis. Tagging works too — the four columns are DJTools' own, and the Sync button, the
rekordbox column and the "not synced" dots are hidden, because nothing is waiting to be sent anywhere.

**Turning it back on** hands those columns over to rekordbox's four My Tag columns, pairing them by
position, and the app says which became which. Everything tagged offline is then queued for the next
sync — nothing is re-done and nothing is lost. `DJTOOLS_RB_DB` implies rekordbox is wanted, so it
overrides a stored off.

## How it fits with rekordbox

1. **Library folder…** → pick `LIBRARY 2.0` on the stick.
2. In rekordbox on this Mac, import that folder and let it analyze. **Rescan** in DJTools picks up
   rekordbox's key/BPM (grey values are from file tags, not rekordbox's analysis).
3. Tag in DJTools (select tracks → tick tags, `F` = favorite). Changes stay local until you
   **Quit rekordbox → Sync to rekordbox**. Each sync backs up `master.db` first to
   `~/Library/Application Support/DJTools/backups/` (last 20 kept).
4. Open rekordbox and export to the stick as usual; the CDJs get the My Tags.

Tracks not yet in rekordbox keep their tags locally and sync once imported.

**Mac vs Windows:** this makes the Mac's rekordbox the collection that holds these tags. Your Windows
collection never sees them (nor does the Mac see its cues/grids/tags), so export from the Mac from now on,
or you'll overwrite the stick with an export that has no My Tags.

On first run the app queues renames of rekordbox's default My Tag columns
(Components → Mood, Situation → Set position, Untitled Column → Favorite). The sync dialog lists them;
rename a column (✎) before syncing if you'd rather keep other names.

## Finding tracks that mix

Under the search box: a **BPM** range (half and double tempo match too, so 70 fits a 140 set), a **key** with
**+ compatible** (±1 on the Camelot wheel and the relative major/minor), and **Untagged**. **⌘K** (or right-click →
*Show tracks that mix with this one*, vim `c`) fills them from the playing track, or the current one: compatible keys
and BPM ±6% (*View → BPM range for mixing…*). **⇧⌘K** clears every filter. Key cells are tinted by Camelot number,
so neighbours on the wheel have neighbouring colours.

In **Filter by tags**, click a tag to cycle ✓ must have → exclude (struck through) → off, and switch between
matching *all* or *any* ticked tag. The number after each tag counts the tracks carrying it in the current list.

## Links: what goes well with what

Remember the transitions that work. Select a track and press **⌘L** (vim `w`, or right-click → *Goes well with…*),
then tick the tracks it goes well with. Select several tracks and **⌘L** links them all together. While a track
plays, right-click another → *Goes well with the playing track*.

The **Links** column counts each track's links (hover for the list). Right-click → *Linked tracks* jumps to one,
**⇧⌘L** (vim `W`) narrows the list to a track and its links, and **⌥⌘L** opens every link in one window: search
them, write a note on a link ("drop on the 2nd break"), play, show in the list or unlink. `⌘Z` undoes linking and
unlinking.

Links are a DJTools thing: they live in its cache on this Mac, not in rekordbox or on the stick. They follow tracks
moved or renamed from DJTools. A file renamed elsewhere shows as *missing* in the Links window until you remove it.

## Tagging faster

**⌘Z / ⇧⌘Z** undo and redo tag changes (ticks, ★, paste), until the next rescan, move or tag rename.
**⌘C** copies the current track's tags, **⌘V** adds them to the selected tracks.

**F2** (or right-click → *Edit title / Edit artist*, vim `i` / `a`) edits the title or artist in place and writes it
into the file; the filename stays as is (Clean up names renames files). In rekordbox, *Reload Tag* shows the change.

## Adding tracks

Drag files or folders from Finder onto a folder in the left pane, or onto the track list (it goes to the folder
being shown, else `A TRIER`), or use **Library → Import tracks… (⌘I)**. Files are copied, never overwritten; a
dropped folder keeps its name. Then import and analyze them in rekordbox and Rescan.

## Layout

Window size, pane sizes, column widths, order, sort and the volume are remembered. Right-click the column header
to show or hide columns (Format and Bitrate are hidden by default). *View → Reset layout* goes back to the defaults.

Files: drag tracks onto a folder to move them (rekordbox's path follows at the next sync). Delete moves
to the macOS Trash; rekordbox then lists the track as missing, remove it there.

## Keeping the library clear

**Library health** (bottom left) shows how many tracks are complete (artist, title, clean name, imported and
analyzed in rekordbox, a Genre tag) and lists what's left: red ✗ lines count toward "complete", orange ! lines
are worth a look. Click a line to show those tracks and read what it means and how to fix it; some have a fix
button (move A TRIER copies or empty folders to the Trash, fix names).

**🧹 Clean up names…** previews title, artist and filename fixes using the same rules as `MusicLib.ps1`
("Title - Artist" filenames in the main artist's folder, no Camelot key, "(Original Mix)" in brackets…), plus a
few of its own: track numbers ("02. ") and quotes are stripped, "/", ";" and "，" count as artist separators
(unless the filename or an existing folder shows the "/" is part of the name, like Burger/Ink), and
"Various Artists" counts as no artist. Lines marked ✗ need a look and aren't ticked. Only title and artist are
written into the files; renames reach rekordbox at the next Sync (use *Reload Tag* in rekordbox to refresh
titles). Each run is logged to `~/Library/Application Support/DJTools/logs/`.

Keys in the table: `Space` play/pause · `Enter` play · `←`/`→` seek 10 s · `F` favorite · `F2` edit title · `⌫` move to Trash.
Menus: `⌘F` search · `⌘R` rescan · `⌘I` import · `⌘K` tracks that mix · `⇧⌘K` clear filters · `⌘Z` undo tags and links ·
`⌘C`/`⌘V` copy/paste tags · `⌘L` link · `⇧⌘L` linked tracks · `⌥⌘L` all links · `⌘/` all shortcuts.

Vim keys: `j`/`k` (with counts), `gg`/`G`, `Ctrl-d`/`Ctrl-u`, `v` visual selection, `o` play, `h`/`l` seek,
`dd`/`x` trash, `m` move, `/` search, `t` tags, `c` tracks that mix, `w`/`W` link / linked tracks, `u`/`Ctrl-r` undo/redo, `y`/`p` copy/paste
tags, `i`/`a` edit title/artist, `Ctrl-h`/`Ctrl-l` switch panes. Press `?` for the full list.

Testing against a copy of the database: `DJTOOLS_RB_DB=/path/to/copy/master.db ./run.sh`
# DJTools
