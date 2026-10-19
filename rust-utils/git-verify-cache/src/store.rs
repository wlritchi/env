//! Packed cache store: 256 append-only shard files, selected by the first
//! byte of the key.
//!
//! Record layout (little endian):
//!   magic u32 | body length u32 | checksum [u8; 8] | body
//!   body = key [u8; 32] | expires_at u64 | stdout length u32 | stdout | stderr
//!
//! The checksum is the first 8 bytes of SHA-256 over the body. A reader stops
//! at the first bad record (for example a torn write). It must not search
//! forward for the next magic value: stored output can contain text from the
//! signer, such as an OpenPGP user ID, and that text could hold a complete
//! record with a valid checksum. Before a writer appends, it removes a bad
//! tail, so that new records are not hidden behind it.
//!
//! Writers do not lock. Each append is one write to a file opened with
//! O_APPEND. Compaction writes a new file and renames it over the shard, so a
//! concurrent append can be lost. For a cache that is acceptable.

use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

pub type Key = [u8; 32];

const MAGIC: u32 = 0x3143_5657; // "WVC1"
const HEADER: usize = 16;
const BODY_FIXED: usize = 32 + 8 + 4;
const MAX_BODY: usize = 16 << 20;
pub const COMPACT_THRESHOLD: u64 = 64 << 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub key: Key,
    pub expires_at: u64,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

impl Entry {
    fn encode(&self) -> Vec<u8> {
        let mut body = Vec::with_capacity(BODY_FIXED + self.stdout.len() + self.stderr.len());
        body.extend_from_slice(&self.key);
        body.extend_from_slice(&self.expires_at.to_le_bytes());
        body.extend_from_slice(&(self.stdout.len() as u32).to_le_bytes());
        body.extend_from_slice(&self.stdout);
        body.extend_from_slice(&self.stderr);
        let mut record = Vec::with_capacity(HEADER + body.len());
        record.extend_from_slice(&MAGIC.to_le_bytes());
        record.extend_from_slice(&(body.len() as u32).to_le_bytes());
        record.extend_from_slice(&Sha256::digest(&body)[..8]);
        record.extend_from_slice(&body);
        record
    }
}

fn u32_at(data: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(data.get(at..at + 4)?.try_into().ok()?))
}

/// Decode the record at `at`. Return the entry and the offset after it.
fn decode_at(data: &[u8], at: usize) -> Option<(Entry, usize)> {
    if u32_at(data, at)? != MAGIC {
        return None;
    }
    let length = u32_at(data, at + 4)? as usize;
    if !(BODY_FIXED..=MAX_BODY).contains(&length) {
        return None;
    }
    let checksum = data.get(at + 8..at + HEADER)?;
    let body = data.get(at + HEADER..at + HEADER + length)?;
    if &Sha256::digest(body)[..8] != checksum {
        return None;
    }
    let key: Key = body[..32].try_into().ok()?;
    let expires_at = u64::from_le_bytes(body[32..40].try_into().ok()?);
    let stdout_length = u32_at(body, 40)? as usize;
    let stdout = body.get(BODY_FIXED..BODY_FIXED + stdout_length)?.to_vec();
    let stderr = body[BODY_FIXED + stdout_length..].to_vec();
    Some((
        Entry {
            key,
            expires_at,
            stdout,
            stderr,
        },
        at + HEADER + length,
    ))
}

/// Decode the valid records in file order, up to the first bad record.
/// Return the records and the length of the valid part.
pub fn decode_all(data: &[u8]) -> (Vec<Entry>, usize) {
    let mut entries = Vec::new();
    let mut at = 0;
    while let Some((entry, next)) = decode_at(data, at) {
        entries.push(entry);
        at = next;
    }
    (entries, at)
}

pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn new(dir: PathBuf) -> Self {
        Self { dir }
    }

    pub fn shard_path(&self, key: &Key) -> PathBuf {
        self.dir.join(format!("{:02x}.log", key[0]))
    }

    /// Return the newest record for `key` that has not expired at `now`.
    pub fn get(&self, key: &Key, now: u64) -> Option<Entry> {
        // Check the checksum of every record, also records with other keys.
        // A torn record can have a length that reaches into the next record,
        // so only a verified record gives a safe offset for the next one.
        let data = fs::read(self.shard_path(key)).ok()?;
        decode_all(&data)
            .0
            .into_iter()
            .rfind(|entry| &entry.key == key && entry.expires_at > now)
    }

    pub fn put(&self, entry: &Entry, now: u64) -> std::io::Result<()> {
        fs::create_dir_all(&self.dir)?;
        let path = self.shard_path(&entry.key);
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(&path)?;
        let mut data = Vec::new();
        file.read_to_end(&mut data)?;
        let (_, valid) = decode_all(&data);
        if valid < data.len() {
            file.set_len(valid as u64)?;
        }
        file.write_all(&entry.encode())?;
        if file.metadata()?.len() > COMPACT_THRESHOLD {
            compact(&path, now)?;
        }
        Ok(())
    }
}

/// Keep the newest unexpired record for each key.
pub fn compact(path: &Path, now: u64) -> std::io::Result<()> {
    let data = fs::read(path)?;
    let mut newest: HashMap<Key, usize> = HashMap::new();
    let (entries, _) = decode_all(&data);
    for (index, entry) in entries.iter().enumerate() {
        if entry.expires_at > now {
            newest.insert(entry.key, index);
        } else {
            newest.remove(&entry.key);
        }
    }
    let mut kept: Vec<usize> = newest.into_values().collect();
    kept.sort_unstable();
    let mut output = Vec::new();
    for index in kept {
        output.extend_from_slice(&entries[index].encode());
    }
    let temporary = path.with_extension(format!("tmp{}", std::process::id()));
    fs::write(&temporary, &output)?;
    fs::rename(&temporary, path).inspect_err(|_| {
        let _ = fs::remove_file(&temporary);
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(first: u8, tag: u8, expires_at: u64) -> Entry {
        let mut key = [tag; 32];
        key[0] = first;
        Entry {
            key,
            expires_at,
            stdout: vec![tag; 5],
            stderr: vec![tag; 3],
        }
    }

    #[test]
    fn round_trip_and_newest_wins() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        let first = entry(7, 1, u64::MAX);
        let mut second = first.clone();
        second.stdout = b"newer".to_vec();
        store.put(&first, 0).unwrap();
        store.put(&entry(7, 2, u64::MAX), 0).unwrap();
        store.put(&second, 0).unwrap();
        assert_eq!(store.get(&first.key, 0), Some(second));
        assert_eq!(store.get(&entry(7, 3, 0).key, 0), None);
    }

    #[test]
    fn expired_entries_miss() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        let item = entry(1, 1, 100);
        store.put(&item, 0).unwrap();
        assert!(store.get(&item.key, 99).is_some());
        assert!(store.get(&item.key, 100).is_none());
    }

    #[test]
    fn append_after_torn_record_removes_the_torn_tail() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        let before = entry(3, 1, u64::MAX);
        let after = entry(3, 2, u64::MAX);
        store.put(&before, 0).unwrap();
        let path = store.shard_path(&before.key);
        let torn = entry(3, 9, u64::MAX).encode();
        let mut file = OpenOptions::new().append(true).open(&path).unwrap();
        file.write_all(&torn[..torn.len() - 4]).unwrap();
        assert_eq!(store.get(&before.key, 0), Some(before.clone()));
        store.put(&after, 0).unwrap();
        assert_eq!(store.get(&before.key, 0), Some(before));
        assert_eq!(store.get(&after.key, 0), Some(after));
        assert_eq!(store.get(&entry(3, 9, 0).key, 0), None);
        let data = fs::read(&path).unwrap();
        assert_eq!(decode_all(&data).1, data.len());
        assert_eq!(decode_all(&data).0.len(), 2);
    }

    #[test]
    fn record_inside_a_bad_record_is_never_read() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        // Signer-controlled output that holds a complete, valid record.
        let smuggled = entry(8, 7, u64::MAX);
        let mut outer = entry(8, 1, u64::MAX);
        outer.stdout = [b"uid ".as_slice(), &smuggled.encode(), b"\n"].concat();
        let mut record = outer.encode();
        record.truncate(record.len() - 1);
        fs::create_dir_all(dir.path()).unwrap();
        fs::write(store.shard_path(&outer.key), &record).unwrap();
        assert_eq!(store.get(&smuggled.key, 0), None);
        assert_eq!(store.get(&outer.key, 0), None);
        // An intact outer record also hides it: it is only output.
        let intact = Store::new(dir.path().join("intact"));
        let mut outer = outer.clone();
        outer.key[1] = 2;
        intact.put(&outer, 0).unwrap();
        assert_eq!(intact.get(&smuggled.key, 0), None);
        assert_eq!(intact.get(&outer.key, 0), Some(outer));
    }

    #[test]
    fn corrupt_body_is_rejected() {
        let mut record = entry(4, 1, u64::MAX).encode();
        let last = record.len() - 1;
        record[last] ^= 1;
        assert_eq!(decode_all(&record), (vec![], 0));
    }

    #[test]
    fn compaction_keeps_newest_unexpired() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        let live = entry(5, 1, u64::MAX);
        let mut newer = live.clone();
        newer.stdout = b"newer".to_vec();
        store.put(&live, 0).unwrap();
        store.put(&entry(5, 2, 10), 0).unwrap();
        store.put(&newer, 0).unwrap();
        let path = store.shard_path(&live.key);
        compact(&path, 50).unwrap();
        assert_eq!(decode_all(&fs::read(&path).unwrap()).0, vec![newer]);
    }

    #[test]
    fn put_compacts_past_threshold() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::new(dir.path().to_path_buf());
        let mut item = entry(6, 1, u64::MAX);
        item.stdout = vec![0; 1024];
        for _ in 0..100 {
            store.put(&item, 0).unwrap();
        }
        let size = fs::metadata(store.shard_path(&item.key)).unwrap().len();
        assert!(size < COMPACT_THRESHOLD, "{size}");
        assert_eq!(store.get(&item.key, 0), Some(item));
    }
}
