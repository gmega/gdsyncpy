# gdsyncpy 
A flexible file synchronization and deduplication tool for Google Drive.

## Introduction
I wrote `gdsyncpy` out of my frustration with Google Drive's Desktop client. I had a large collection of pictures
laying around an old hard drive (over `15,000` of them), and needed to accomplish two simple tasks: 

* *Backup.* If a picture was present on my hard drive (anywhere) but not on Google Drive (anywhere), then I wanted it 
to be copied to Google Drive. I also wanted it to appear in the Google Photos stream.

* *Deduplication.* Since I had been copying stuff into Google Drive (and Google Photos) in a disorganized 
fashion for a number of years, I wanted a simple way to hunt down and eliminate all duplicate pictures in my 
Google Drive. 

Turns out I couldn't accomplish either task reliably with the Google Drive Client. Indeed, after a long round of 
e-mails with Google's support, I ended up with some pictures not being backed up, while others got duplicated both on 
Google Drive and in my old hard drive. Not great.

With `gdsyncpy` I finally managed to wrap up my backup and deduplication efforts. If you want to see how, just skip to 
[Use Case: My Photo Collection.](#use-case-my-photo-collection)

 
## Backing Up (Syncing)

The easiest way to backup a set of pictures not currently in Google Drive is to run the `sync`
command. For instance, the following command will copy all of the pictures in `/OldHardDrive/` into a Google Drive folder
named `/Pictures/`, under `/My Drive/`: 

```bash
gdsyncpy sync  /OldHardDrive/ /Pictures/ --exclude-folder / --include-media-only --include-recursive  
```

With this command, `gdsyncpy` will:
 
 1. locate all [media files](#media-files---include-media-only) 
 within `/OldHardDrive/`, including those present in subdirectories (`--recursive` flag); 
 
 2. recursively scan for all files within Google Drive folder `/My Drive/` (`--exclude-folder /`);
 
 3. copy to `/My Drive/Pictures/` all media files which are present in `/OldHardDrive/` (which were scanned in **(1)**) 
 and are **not** present in `/My Drive/` (which were scanned in **(2)**).

Files will be copied from `/OldHardDrive/` into `/My Drive/Pictures/` without preserving the parent directory structure.
This means that if `/OldHardDrive/` contained two files:

* `/OldHardDrive/Album1/1.png`
* `/OldHardDrive/Album2/2.png`

those would be copied into `/My Drive/Pictures/1.png` and `/My Drive/Pictures/2.png`, respectively. This is done this way
because I did not care about the originating folder structure, I simply cared about making sure all pictures were backed
up and visible in the Google Photos timeline. My albums were later organized using Google Photos.

**N.B.** If the **sync** fails by any chance, you can always attempt to [Resume a Failed Sync]().

### Sync Caveats
`gdsyncpy sync` works by comparing MD5 hashes. Since Google Drive does not provide MD5 hashes for all MIME types, 
let's refer to files of these types as _unhashable_ files. Attempting to sync a source folder containing _unshashable_ 
files will cause `gdsyncpy` to repeatedly copy these file into Google Drive, creating duplicates it cannot later remove 
(as it cannot see their MD5 hashes anywhere).

There are a number of ways to avoid this most annoying situation:

1. always use the `--include-media-only` switch. This will guarantee that _unhashable_ files never get scooped;
2. if `--include-media-only` is overly strict, or is doing a poor job at recognizing your media, refrain from syncing 
folders which contain file types which are not media files (pictures, video, audio);
3. and last, but not least, the most obvious solution: do not sync folders which contain _unhashable_ files, if you know
what those are. I never bothered to narrow the actual MIME types down, but could do it if there's interest from someone.

## Deduplication

Deduplication involves two activities:

1. [finding and listing duplicates](#listing-duplicates);
2. [deciding which of the duplicates to keep as "original" and deleting the unwanted copies](#performing-deduplication).

`gdsyncpy` provides tools to help with both.

### Listing Duplicates

To list duplicates, you can run the `dedup list` command against a folder or a [snapshot](#snapshots). For instance, 
to list the all of the duplicates in a folder named `/My Drive/My Folder/` folder, you would run:

```bash
gdsyncpy dedup --folder '/My Folder/' list
```

And `gdsyncpy` will dump all duplicates on screen, grouped by their [md5](https://en.wikipedia.org/wiki/MD5) hash. 

Often, however, this output is too messy as you cannot see patterns from raw output. In those cases, you will want to 
post-process the results somehow. To ease with post-processing, `gdsyncpy` offers the `--json` switch which outputs
JSON instead of text. Using `--json` with [jq](https://stedolan.github.io/jq/) and some 
[GNU coreutils](https://www.gnu.org/software/coreutils/) you could post-process the output to visualize,
 for instance, which are the folders that concentrate the most duplicates. The line:

 
```bash
> gdsyncpy dedup --folder '/' list --json  | jq '.[] | .[] | .path' | cut -d '/' -f 1-3 | sort | uniq -c 
```

would output, in my Google Drive:

```
Google Drive authentication successful.
Computing duplicates and resolving resource paths.
Duplicates were found.

      6 /My Drive/Google Photos
     11 /My Drive/Minipics
   2042 /My Drive/Pictures
   5213 /My Drive/PicturesNew
```

This tells me that most duplicates are in `Pictures` and `PicturesNew`, and those are probably the folders I should
focus on.

### Performing Deduplication

Deduplication is applied by means of the `gdsyncpy dedup apply` command. Deduplication is a straighforward process but 
for one aspect: you need to tell `gdsyncpy` which pictures to keep, and which to 
delete. The way this is done is by specifying a _prefix list_ with `--prefixes` option, which will then guide 
`gdsyncpy` in making this decision for each picture.

A _prefix list_ is a comma-separated list of paths into Google Drive; i.e., any path can be used as a prefix. 
`/foo/bar/,/baz/boo/,/a/b/c/` is an example of a prefix list containing three prefixes. 

`gdsyncpy` will interpret a prefix list as a declaration of your _preferences_. For the list in the example, this tells 
`gdsyncpy` that, should it encounter two duplicate files in Google Drive, it should keep the copy in the leftmost
prefix as the original, and delete the others as duplicates.

For instance, if we had duplicates for a file named `a.jpg` under `/foo/bar/a.jpg` and `/baz/boo/a.jpg`, 
`gdsyncpy` would keep `/foo/bar/a.jpg` and delete the file under `/baz/boo/a.jpg`. Same thing if we had 
`/baz/boo/a.jpg` and `/a/b/c/a.jpg`: `gdsyncpy` would, in this case, delete the copy under `/a/b/c/a.jpg` 
and keep the one under `/baz/boo/a.jpg`. 

If more than one duplicate lives under the same prefix, `gdsyncpy` will keep one at random and delete the rest. 
For instance, if you specify `/` as a prefix, `gdsyncpy` will keep exactly one copy of each file under Google Drive,
deleting all duplicates at random. 

An example deduplication command could, therefore, be:

```bash
gdsyncpy dedup --folder '/' apply --prefixes '/' --dry-run 
```

Note the use of the `--dry-run` option, which does a simulated run of the `apply` operation witout making changes to
Google Drive.

As a real-world example, I used the following command for deduplicating my Google Drive account 
after analysing the [output from the `list` operation](#listing-duplicates) in my root folder:

```bash
gdsyncpy dedup --folder '/' apply --prefixes '/My Drive/Google Photos,/My Drive/Pictures,/My Drive/PicturesNew,/My Drive/Minipics'
``` 

## Snapshots

Snapshots are a way of saving metadata about the contents of a Google Drive folder so that they can be reused in other 
tasks such as de-duplication and sync without requiring a new set of calls to the Google Drive API. If you intend to run several 
operations which look at the contents of a folder without modifying it, snapshots can speed things up significantly. 
To create a snapshot of a Google Drive folder under path `/My Drive/Old Pictures/` you would run:

```bash
gdsyncpy snapshot '/Old Pictures' ./old-pictures-snapshot.json
```

Contents of snapshotted folders can then be used for analysis with the **dedup** command, or excluded from a **sync** 
with the `--exclude-snapshot` command.

## Media Files (--include-media-only)
Media files are taken to be any audio (e.g. `.mp3`), video (e.g. `.avi, .mov, .ogg`), or image files (e.g. 
 `.png, .jpg, .bmp`). I chose those as they are the types of files we are usually more interested in backing up, 
 and because I wanted to simply run `gdsyncpy` at the root of my hard drive and let it copy everything without 
 having to worry about it picking up lost spreadsheets and other nonsense.
 
## Resume a Failed Sync

When syncing a large file collection (e.g., thousands of pictures) you will almost certainly run into issues. Your 
Wi-Fi will drop, someone will trip over your network cable, you'll experience a power outage, or `gdsyncpy` will crash
because it got some unexpected response from the Google Drive API. 

Those are _transient failures_, and simply resuming the sync from where it crashed is very likely to solve your
problem (unless someone trips over your network cable an infinite amount of times). To resume a sync that crashed, you
can use:

```bash
gdsyncpy resume
```

and `gdsyncpy` will remember what it was doing and pick up from where it left off. 

## Use Case: My Photo Collection

My photo collection was a complete mess. I had part of my files stored under `/My Drive/Google Photos/` (after I [enabled
integration of Google Drive & Google Photos](https://support.google.com/photos/answer/6156103)), part stored under 
`/My Drive/Pictures/`, and part under yet other folders I did not even remember existed. I therefore started by 
creating a snapshot of the whole contents of `/My Drive/` for analysis:

```
gdsyncpy snapshot '/' ./full-20190220.json
```

### Deduplication
Since I most likely had massive duplication in my Google Drive, I proceeded by scanning the snapshot for duplicates 
and feeding the output to [jq](https://stedolan.github.io/jq/) and some 
[GNU coreutils](https://www.gnu.org/software/coreutils/) to see where duplicates, if any, were concentrated.

```bash
gdsyncpy dedup --snapshot './full-20190220.json' list --json  | jq '.[] | .[] | .path' | cut -d '/' -f 1-3 | sort | uniq -c 
```

This generated the output:

```
Google Drive authentication successful.
Computing duplicates and resolving resource paths.
Duplicates were found.

      6 /My Drive/Google Photos
     11 /My Drive/Minipics
   2042 /My Drive/Pictures
   5213 /My Drive/PicturesNew
```

From this, I could infer that the biggest concentrators of my duplicates were `PicturesNew` and `Pictures`. I then put 
together my prefix preferences: 

* `Google Photos` is my most preferred prefix, I always want to keep everything that's in there;
* in case of duplicates, first delete the ones under `PicturesNew` and `Pictures`, then the ones under `Minipics`.

My prefix list became:
 
* `'/My Drive/Google Photos,/My Drive/Pictures,/My Drive/PicturesNew,/My Drive/Minipics`.

The next step was to dry-run my deduplication as follows:
```bash
gdsyncpy dedup --snapshot ./full-20190215.json apply --prefixes '/My Drive/Google Photos,/My Drive/Pictures,/My Drive/PicturesNew,/My Drive/Minipics' --dry-run
```
   
Once I checked that it was performing the actions I had intended by quickly skimming through the output, I ran the actual
deduplication without the `--dry-run` switch: 

```bash
gdsyncpy dedup --snapshot ./full-20190215.json apply --prefixes '/My Drive/Google Photos,/My Drive/Pictures,/My Drive/PicturesNew,/My Drive/Minipics'
```

which blissfully purged my Google Drive from all ancient duplicates.

### Syncing

Once deduplication was done, I ran the sync. 

```bash
gdsyncpy sync '/OldHardDrive/' '/PicturesNew/' --exclude-snapshot ./full-20190215.json --include-recursive --allow-duplicates
```

And, surely as the sun will rise, my sync eventually failed because somewhere along syncing some 7,000 pictures I had
a network failure. Once the network came back up, I resumed my sync with:

```bash
gdsyncpy resume
``` 

and eventually the sync ran its course and all of my Pictures were in Google Drive (and Google Photos). Finally! 
