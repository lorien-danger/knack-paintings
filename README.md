# Paintings

158 of the world's best-known paintings, all in the public domain, by 69 painters from Europe, the Americas,
East and South Asia, the Middle East and Australia, painted between 1010 and 1930. Each has its picture, title,
painter, year, movement, the museum (or church or palace) where it hangs, that place's city and where it is on the
globe. Two embeddings are included: how each picture looks and how the painter's name sounds. A dataset for
[Knack](https://github.com/lorien-danger/knack): add it by this repo's link.

Most painters have two to four paintings here, so "who painted this?" has something to learn from. Five have one:
Andrei Rublev, Fan Kuan, Guo Xi, Hasegawa Tōhaku and Grant Wood.

Suggested practice: *Who painted it?* (picture, painter), *Name the painting* (picture, title) and *When and
where?* (picture, year, where it hangs).

## Columns

| column | type | what it is |
| --- | --- | --- |
| picture | image | the painting, at most 640 pixels on its longer side (`media/<id>.jpg`, 11 MB in all) |
| title | plain | its usual English title |
| painter | plain | who painted it |
| year | number | when it was painted; for a range, the middle year (see *Years* below) |
| movement | tree | part of the world › family of movements or country › movement, e.g. Europe › Impressionism and after › Post-Impressionism |
| museum ("Where it hangs") | plain | the museum, church or palace that holds it now |
| city | plain | the city that place is in |
| location ("Where in the world") | sphere | that place's latitude and longitude |
| look | embedding | the picture, DINOv2 (`facebook/dinov2-base`), 64 numbers: `look.npy` |
| painter sound | embedding | the painter's name, ByT5 pronunciation model (`bookbot/onnx-byt5-small-wikipron-eng-latn-au-broad`), 64 numbers: `painter-sound.npy` |

Pictures are shown whole in their own shape (`"fit": "contain"`, no `aspect`): the paintings run from tall scrolls
(0.46 wide for 1 high) to wide screens (2.7 to 1), and any fixed frame that filled with them would cut off much of
one or the other.

The embeddings are PCA-reduced to 64 numbers and scaled so a typical row is about 1 from the centre, as the app does.
The full-size DINOv2 vectors (768 numbers) are in `build/look-full.npy`.

## Sources

- **Facts**: [Wikidata](https://www.wikidata.org/) (CC0). Every row comes from a Wikidata item, listed with it in
  `build/paintings.tsv` and `build/credits.tsv`: the title is the item's English label, the painter its creator
  (P170), the year its inception (P571), the museum its current collection (P195, preferring the one that is also
  its location, P276), the city the first place the museum is in (P131) that is a city, and the location the
  museum's coordinates (P625).
- **Where Wikidata is missing, too coarse or wrong**, `build/fixes.json` gives the value by hand and says where each
  came from: mostly the English Wikipedia article (an infobox year, an article title), once the Russian one, once
  the Art Institute of Chicago's own title. It also shortens eight titles too long for a button to their usual short
  form (*Where Do We Come From?*, *A Sunday on La Grande Jatte*, *The Falling Rocket*...) and names the Oslo
  National Museum by its own name, *Nasjonalmuseet*.
- **Movements**: `build/movements.json`. Each painter is given one movement, taken from their Wikidata item's
  movement (P135), or where it has none from their English Wikipedia article (each entry says which); three paintings
  have their own (Manet's *A Bar at the Folies-Bergère*, Sargent's *Madame X*, Velázquez's *Innocent X*). The levels
  above the movement (part of the world, then a family of movements or a country) are this dataset's own grouping.
- **Pictures**: [Wikimedia Commons](https://commons.wikimedia.org/), the file Wikidata names for each painting
  (P18), except two where that file's photograph carried a CC BY or CC BY-SA licence and a public-domain copy was
  used instead. Every picture used is marked public domain (155) or CC0 (3) on Commons; `build/credits.tsv` lists
  each file, its Commons page, its licence and its author as Commons gives them. They are scaled down and stored
  here, so they load quickly and don't depend on anyone else's server.

### Public domain

Every painter died before 1956 (more than 70 years ago) and every painting is dated before 1931, so each is in the
public domain in countries whose term is the author's life plus 70 years, and in the United States. The build
stops if a painting is not. The latest are Grant Wood's *American Gothic* and Mondrian's *Composition II in Red,
Blue, and Yellow* (both 1930; Wood died 1942, Mondrian 1944). Countries with longer terms (Mexico, life plus 100) do not yet cover the thirteen painters here who died in 1926 or
later: Monet, Mary Cassatt and Vasnetsov (1926), Repin (1930), Tom Roberts and Gallen-Kallela (1931), Malevich
(1935), Grant Wood (1942), Streeton (1943), Munch, Mondrian and Kandinsky (1944) and Abanindranath Tagore (1951).

### Years

The year is Wikidata's when Wikidata knows it to the year. Where Wikidata gives a range (two inception dates, or
earliest and latest dates) the year is the middle of it, rounded down: *Netherlandish Proverbs*, 1551–1559, is 1555.
Where Wikidata knows only the decade or century, the English Wikipedia infobox's year or range is used instead, and
failing that the middle of Wikidata's decade (Botticelli's *The Birth of Venus*: 1478–1489, 1483). Where the text
gives two ranges, the first is used (*Mona Lisa*: "c. 1503–1506, perhaps continuing until c. 1517" gives 1504).
`build/years.tsv` lists every painting with Wikidata's dates, the range, the year given and where it came from.

## Licence

The dataset (`columns.json`, `rows.jsonl`, the embeddings and the build scripts) is released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (`LICENSE`), like the Wikidata it is built from. The
pictures are reproductions of public-domain paintings and are themselves public domain or CC0, as listed in
`build/credits.tsv`. A few facts come from Wikipedia (CC BY-SA); they are single dates and names, credited in
`build/fixes.json` and `build/movements.json`.

## Building it

`build/` makes the dataset. It needs Python with pillow (and numpy, torch and transformers for the picture
embedding), and the Knack app checked out somewhere for the name embedding.

    cd build
    python candidates.py      # optional: up to 20 paintings per painter in painters.txt, by fame -> candidates.tsv
    python build.py           # paintings.tsv -> ../rows.jsonl and ../media/ (+ credits.tsv, years.tsv)
    python embed.py           # ../look.npy, and the "look" column in ../columns.json (about a minute)
    cd <knack> && npm run describe -- <this folder> painter   # painter-sound.npy, and its column

`paintings.tsv` is the choice of paintings, made by hand from `candidates.tsv` (the most-written-about paintings of
each painter, by how many Wikipedias have an article on them) plus a few famous ones that Wikidata files under a
series (*The Scream*, *Sunflowers*, *The Card Players*, *Water Lilies*: the version in the National Museum, Oslo,
the National Gallery, London, the Musée d'Orsay and the National Museum of Western Art, Tokyo). Woodblock prints
(Hokusai, Hiroshige) are left out: they are prints, in many copies, not paintings held by one museum.
`build.py` caches what it downloads in `build/.cache/` (not in this repo).
