How `scan_one` Matches Extents
==============================

The `scan_one` algorithm is the core of how bees (v0.11 and earlier)
decides whether two btrfs extents share data that can be replaced with
a reference to a single copy.  This page explains the matching process
and, in particular, how the key sizing and eviction parameters affect
whether any two duplicate extents actually get deduplicated:

 * [hash table size](#hash-table-size)
 * [data extent size](#data-extent-size)
 * [the 50% must-free threshold](#the-50-must-free-threshold)
 * [the random-insertion-order hash LRU](#random-insertion-order-hash-lru)
 * [the 256 cells per hash bucket](#256-cells-per-bucket)

This is intended for users who are tuning bees or trying to understand
why some duplicates are found and others are not.  For a shorter,
higher-level description of the daemon, see [how bees works](how-it-works.md).

The `scan_one` Algorithm, in Brief
----------------------------------

For each btrfs data extent bees encounters during a scan:

 1. **Read and hash the extent**, one 4 KiB block at a time.  All-zero
    blocks are recognized and set aside as "punch a hole" candidates
    rather than hashed for dedupe.

 2. **For each non-zero block, look up the hash in the hash table.**
    A hit means some previously-scanned block on the filesystem had
    the same hash.

 3. **For each hit, ask the kernel where that block lives** (via the
    `LOGICAL_INO` ioctl) and try to **extend the match** — see how
    far forward and backward from the hit the two extents agree.
    A single matching block in the new extent can anchor a much
    longer run of adjacent duplicate blocks.

 4. **Pick the longest non-overlapping matched ranges** and check the
    nuisance-dedupe filter:  the number of bytes that would be *freed*
    (by dedupe plus zero-punching) must be at least the number of bytes
    that would have to be *rewritten* — the non-matching parts of the
    new extent, whose references have to be relocated off the original
    extent so that no reference to any part of the original remains
    and btrfs can free it.  If not, the dedupe is skipped.

 5. **Perform the dedupes** via the `FILE_EXTENT_SAME` ioctl.

 6. **Update the hash table.**  Hashes that produced a dedupe match
    are moved to the front of their LRU list.  Hashes from the new
    extent that were not deduped are inserted at a random position
    in their bucket.

A duplicate extent is only eliminated when steps 2, 3, and 4 all
succeed.  Each parameter below controls one of those steps.

When Does Dedupe Actually Happen?
---------------------------------

Two extents `A` (older, already scanned) and `B` (newer, being scanned
now) get deduplicated only if **all three** of the following are true:

 * **Gate A — at least one hash from `A` is still in the hash table**
   when `B` is scanned.
 * **Gate B — at least one block of `B` collides with a retained hash
   from `A`.**
 * **Gate C — the extension pass finds enough contiguous matching
   data to pass the nuisance-dedupe filter.**

Most tuning questions are really questions about one of these three
gates.  The rest of this page maps each parameter onto the gate (or
gates) it controls.

Hash Table Size
---------------

The hash table holds a fixed number of (hash, physical-address) pairs,
one pair per 16 bytes of table.  A 1 GB hash table holds about 64 M
pairs — one per 4 KiB block it can remember.

**Effect on Gate A.**  Hash table size is the dominant control on how
long a scanned block's hash survives in the table.  The intuition: if
your filesystem has much more unique-block data than the table can hold,
each slot is overwritten on average after a number of new inserts equal
to the number of slots, so older data drops out first.

This is why the bees rule of thumb is "1 GB of hash table per 10 TB of
unique data":  it keeps the expected survival time of a hash long enough
to overlap with the scan period for most real workloads, where
duplicates tend to appear relatively near each other in time.

Under-provisioning the hash table does **not** prevent dedupe — it
reduces the dedupe *rate*, typically most on the oldest data.

Data Extent Size
----------------

btrfs extents can be up to 128 MiB for uncompressed data (128KiB
for compressed data).  Extent size affects two gates.

**Effect on Gate B.**  Gate B asks whether *any* block of the new
extent hits a retained hash.  If an extent has `n` blocks and each
block has independent probability `p` of having its hash retained,
then the probability of at least one hit is

                P(hit) = 1 − (1 − p)^n

Larger extents give bees more independent "shots" at finding an
anchor block.  A 16-block extent (64 KiB) and a 256-block extent
(1 MiB) behave very differently, even with the same hash table.

This is why [how bees works](how-it-works.md) says most of the hashes
in the hash table are redundant:  you don't need *every* block of an
extent retained, you only need one, because the extension pass in
step 3 will find the rest.

**Effect on Gate C.**  Larger extents also raise the bar for the
nuisance filter:  to pass, more of the extent has to dedupe.  A small
extent is almost always either fully duplicate or fully unique; a
large extent is more likely to have only a fraction of its content
match.

Net effect:  medium-to-large extents are the sweet spot for `scan_one`.
Very small extents don't benefit from the multi-shot retention.  Very
large, partially-duplicate extents get skipped by the nuisance filter.

The 50% Must-Free Threshold
---------------------------

btrfs cannot free part of an extent — an extent is allocated and
freed as a whole.  As long as *any* file anywhere in the filesystem
references *any* part of an extent, the whole extent stays
allocated, including blocks that no file references.  Recovering
the space held by an extent therefore means eliminating every
reference to every part of it.

For the matching parts of the new extent, `FILE_EXTENT_SAME`
redirects their references to the duplicate data elsewhere.  For
the non-matching parts, bees rewrites the data into new
allocations, so those file offsets stop pointing at the original
extent.  Once no reference to the original extent remains anywhere,
btrfs frees it.

The rewritten data does not persist as a duplicate — the original
extent is freed, and the rewrite replaces it at those offsets — but
the rewrite still costs I/O, allocator churn, and extent
fragmentation, and those costs have to be paid back in recovered
free space for the dedupe to be worth doing.

bees enforces the rule:

                bytes_freed  ≥  bytes_rewritten

where `bytes_freed = bytes_deduped + bytes_zero_punched` and
`bytes_rewritten` is the size of the non-matching data that must be
rewritten to free the original extent.

For an extent with no zero blocks, this reduces to the well-known
**"at least 50% of the extent must dedupe"** threshold.  When zero
blocks are present — for example, a mostly-empty VM disk image — the
threshold is easier to pass, because punching holes also frees space.

**Effect on Gate C.**  This is the gate itself.  Extents that match
only sparsely (say, 20% shared and 80% unique) are skipped entirely;
the hashes from the unique part are still inserted into the table,
so a future extent that shares more with them can still succeed later.

Random-Insertion-Order Hash LRU
-------------------------------

Each bucket of the hash table is an LRU list of 256 cells.  bees uses
two distinct insertion policies:

 * **Move to front on hit.**  When a hash lookup finds a cell that
   produces a successful dedupe match, that cell is moved to slot 0.
 * **Random position on unique insert.**  When a new, previously-unseen
   hash is inserted, it goes into a *uniformly random* slot in its
   bucket.  If the bucket is full, the cell at the tail (slot 255) is
   evicted, and the cells between the insertion point and the tail are
   shifted back by one.

**Effect on Gate A.**  This is the subtlest of the parameters.
Compared to a plain FIFO (where every cell would live exactly as long
as the bucket depth before eviction), random insertion gives a
*fat-tailed* survival distribution.  A cell that happens to be inserted
near the front of the bucket has a much longer expected lifetime than
one inserted near the back, and that difference is amplified by the
move-to-front promotion:  any hash that ever matches gets effectively
pinned near the front.

The practical consequence:  bees keeps "useful" hashes (ones that match
real duplicates) much longer than "unlucky" hashes (ones that never
match), without needing an expensive explicit usage counter.  It trades
a small probability of missing a rare duplicate for much better
retention of common patterns.

256 Cells Per Bucket
--------------------

Each 4 KiB page of the hash table file is one bucket of 256 cells.
This size is set by the mmap page size and cell width, not a tunable
parameter, but it has two effects worth understanding.

**Effect on Gate A (collision capacity).**  Different physical blocks
with colliding hashes can all coexist in the same bucket, up to 256 of
them.  This matters for widely replicated content such as boilerplate
file headers and common fill patterns:  bees can keep many addresses
for the "same" hash without any one of them crowding out the others.

**Effect on Gate A (LRU depth).**  A 256-slot LRU is a meaningful
buffer against eviction.  If buckets were shallow (say, 4 cells), only
the very-most-recent or very-popular hashes would survive, and
rare-but-real duplicates would almost always miss.  The 256-deep
bucket and the random-insert policy together are what let bees find
old and rare duplicates at a useful rate.

How the Parameters Interact
---------------------------

A few interactions are worth singling out:

 * **Hash table size and extent size compound.**  If the hash table
   is marginal, small extents suffer disproportionately:  their Gate B
   probability `1 − (1 − p)^n` falls quickly with small `n`.  Doubling
   the hash table helps small extents more than large ones.

 * **Extent size fights the 50% threshold.**  Increasing extent size
   makes Gate B easier but Gate C harder.  Which effect dominates
   depends on the file's *content pattern*, not on btrfs:

    * Large, sequentially-written files (media files, log archives,
      installation images) tend to contain long runs where every
      block is unique *within* the file, but the whole sequence
      recurs whenever the file is duplicated.  Match rates of 100%
      are common, so the 50% threshold is met with room to spare.
    * Files written in many small writes in random order (for
      example, databases) tend to contain a mixture of unique and
      duplicate blocks in every extent.  The matched fraction can
      be non-trivial, but the rewrite cost makes the dedupe
      unprofitable, and Gate C filters the extent out.
    * VM disk images mix both patterns — contiguous regions that
      behave like media files and churn regions that behave like
      databases — and produce both outcomes depending on the extent.

   The 50% filter lets bees cherry-pick the extents that are worth
   deduping and skip the ones that would just burn time and metadata,
   without needing to know anything about file type or content
   semantics.

 * **Move-to-front is what keeps the hash table effective in steady
   state.**  Without it, even a well-sized table would slowly turn
   over and forget the data that is actually being deduped.  With it,
   the table quickly learns which hashes are worth keeping and holds
   onto them, regardless of how much non-duplicate traffic is flowing
   through.

Practical Tuning Implications
-----------------------------

 * If you are missing dedupes you expect to see, the most likely cause
   is hash table size (Gate A) or extent layout (Gates B and C).

 * The nuisance filter is intentional and generally should not be
   worked around:  dedupes that rewrite more than they recover
   still gain some free space (the original extent is freed either
   way), but the ratio of recovered space to I/O, allocator churn,
   and metadata cost is too low to be worth doing.

 * The random-insertion LRU combined with move-to-front on match
   biases the hash table toward the hashes that actually produce
   dedupes:  matched hashes are effectively pinned near the front,
   and hashes that are never used drift back until they are evicted.
   Most of the cells in the table at any moment are redundant, but
   that is the cost of not knowing in advance which ones will be
   useful.

 * Continuous and periodic operation are usually equivalent for
   per-extent dedupe probability.  bees persists its hash table and
   scan position, so a restart does not reset the LRU and does not
   skip data, and each extent is scanned in the same order either
   way.  The important exception is workloads that rapidly churn
   the same blocks (rotating logs, continuous rebuilds,
   heavily-updated databases):  continuous bees will scan every
   intermediate version of those blocks and fill the LRU with
   hashes that point to extents that are already freed by the time
   the next duplicate arrives, crowding out more useful hashes.
   Running bees periodically lets short-lived data be overwritten
   before bees ever sees it; increasing the hash table size is the
   other way to compensate.
