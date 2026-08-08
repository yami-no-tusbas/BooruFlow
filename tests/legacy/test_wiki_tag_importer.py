from legacy import wiki_tag_importer as importer

from legacy.wiki_tag_importer import (
    _clean_heading,
    analyze_pasted_tag_list,
    merge_catalogues,
    parse_e621_group,
    parse_gelbooru_group,
    parse_pasted_tag_list,
)


def test_e621_sections_and_nested_tags_are_imported():
    body = """
h3. Fauna
h4. Canine
* [[dog]]
** [[german_shepherd|German Shepherd]]
h4. Marine
* [[fish]]
"""
    tree = parse_e621_group(body)
    fauna = tree["Fauna"]
    assert fauna["Canine"]["dog"]["__tag__"] == "dog"
    assert fauna["Canine"]["dog"]["german_shepherd"]["__tag__"] == "german_shepherd"
    assert fauna["Marine"]["fish"]["__tag__"] == "fish"


def test_e621_nested_sections_reset_bullets_under_their_parent():
    body = """
h3. Navigation:
h5. [[#arthropod|Arthropod]]
* [[#arachnid|Arachnid]]
h3. Fauna
h4. [[Arthropod]]: [#arthropod]
[section,expanded=Arthropod:]
* [[Arachnid]]
[section,expanded=Arachnid:]
* [[Acarine]]
** [[Mite]]
*** [[Acariform]]
**** [[Pyroglyphid]]
***** [[Dust Mite]]
[/section]
* [[Crustacean]]
[/section]
"""
    tree = parse_e621_group(body)
    arthropod = tree["Fauna"]["Arthropod"]
    arachnid = arthropod["arachnid"]
    assert arachnid["acarine"]["mite"]["acariform"]["pyroglyphid"]["dust_mite"]["__tag__"] == "dust_mite"
    assert arthropod["crustacean"]["__tag__"] == "crustacean"
    assert "Navigation" not in tree


def test_e621_named_sections_and_tag_group_links_keep_the_index_hierarchy():
    body = """
[section=Character-based]
h5. Character attire/appearance
* [[tag group:body appearance|Body appearance]]
** [[tag group:markings|Markings]]
[/section]
"""
    tree = parse_e621_group(body)
    attire = tree["Character-based"]["Character attire/appearance"]
    appearance = attire["tag_group:body_appearance"]
    assert appearance["__tag__"] == "tag_group:body_appearance"
    assert appearance["tag_group:markings"]["__tag__"] == "tag_group:markings"


def test_e621_group_page_without_heading_still_exposes_linked_groups():
    body = """
Introductory text.
* [[tag group:body types]]
* [[tag group:species]]
* [[tag group:anatomy]]
h4. Most important tags
* [[anthro]]
"""
    tree = parse_e621_group(body)
    assert tree["tag_group:body_types"]["__tag__"] == "tag_group:body_types"
    assert tree["tag_group:species"]["__tag__"] == "tag_group:species"
    assert tree["Most important tags"]["anthro"]["__tag__"] == "anthro"


def test_e621_plain_hybrid_tags_alias_lists_and_annotations_are_imported():
    body = """
h3. Fauna (Animals)
h4. [[Hybrid]]: [#hybridfauna]
[section,expanded=Hybrid:]
* Bovid
** Dzo, Zo, Yakow
** Zubron (Żubroń)
* Equid
** [s][[Mule]] (also used for non-equid hybrids)[/s]
* Felid
** [[Liger]], tigon
** Pumapard
* Tiger_muskie (fish)
* Zebroid
** Zeedonk, Zonkey
[/section]
* See Also: [[tag group:fictional species]]
"""
    hybrid = parse_e621_group(body)["Fauna (Animals)"]["Hybrid"]
    assert set(hybrid["bovid"]) >= {"dzo", "zo", "yakow", "zubron"}
    assert hybrid["equid"]["mule"]["__tag__"] == "mule"
    assert set(hybrid["felid"]) >= {"liger", "tigon", "pumapard"}
    assert hybrid["tiger_muskie"]["__tag__"] == "tiger_muskie"
    assert set(hybrid["zebroid"]) >= {"zeedonk", "zonkey"}
    assert "tag_group:fictional_species" not in hybrid


def test_e621_import_recursively_expands_linked_tag_group_pages():
    pages = {
        "tag_group:index": {
            "id": 1,
            "body": """
[section=Character-based]
h5. Appearance
* [[tag group:body appearance]]
[/section]
""",
        },
        "tag_group:body_appearance": {
            "id": 2,
            "body": "* [[tag group:species]]\nh4. Important\n* [[anthro]]",
        },
        "tag_group:species": {
            "id": 3,
            "body": "h3. Fauna\nh4. Canine\n* [[Dog]]\n** [[German Shepherd]]",
        },
        "vehicle": {"id": 4, "body": "h3. Vehicles\n* [[Car]]"},
    }
    original_page = importer._e621_page
    original_get = importer._get
    original_relationships = importer._e621_relationships
    try:
        importer._e621_page = lambda title: pages[title]
        importer._get = lambda _url: ""
        importer._e621_relationships = lambda _tag, _types=None: {}
        catalogue = importer.import_catalogues()
    finally:
        importer._e621_page = original_page
        importer._get = original_get
        importer._e621_relationships = original_relationships
    appearance = catalogue["boards"]["e621"]["Character-based"]["Appearance"]
    species = appearance["tag_group:body_appearance"]["tag_group:species"]
    shepherd = species["Fauna"]["Canine"]["dog"]["german_shepherd"]
    assert shepherd["__tag__"] == "german_shepherd"
    assert catalogue["boards"]["e621"]["Vehicle"]["Vehicles"]["car"]["__tag__"] == "car"


def test_e621_ordinary_parent_pages_and_active_relationships_are_expanded():
    pages = {
        "tag_group:index": {"id": 1, "body": "* [[tag group:species]]"},
        "tag_group:species": {
            "id": 2,
            "body": "h4. [[Mammal]]\n* [[Feline]]\n** [[Cat]]\n* [[Cheetah]]",
        },
        "feline": {
            "id": 293,
            "body": (
                "h4. Genera and species\n"
                "* [[felid|felidae]]\n"
                "** [[feline|felinae]]\n"
                "*** [[lynx]]\n"
                "**** [[bobcat]]\n"
                "*** [[felis]]\n"
                "**** [[wildcat]]"
            ),
        },
        "vehicle": {"id": 4, "body": "h3. Vehicles\n* [[Car]]"},
    }
    relations = {
        "mammal": {
            "implicated_by": ["felid"],
        },
        "felid": {
            "implicated_by": ["feline"],
        },
        "feline": {
            "aliases": ["catgirl"],
            "implicates": ["felid"],
            "implicated_by": ["caracal_(genus)", "felis", "miracinonyx"],
        },
        "caracal_(genus)": {
            "implicated_by": ["african_golden_cat", "caracal"],
        },
        "felis": {
            "implicated_by": ["domestic_cat", "wildcat"],
        },
        "wildcat": {
            "implicated_by": ["african_wildcat", "european_wildcat"],
        },
        "domestic_cat": {
            "implicated_by": ["maine_coon", "siamese_cat"],
        },
    }
    original_page = importer._e621_page
    original_get = importer._get
    original_relationships = importer._e621_relationships
    try:
        importer._e621_page = lambda title: pages[title]
        importer._get = lambda _url: ""
        importer._e621_relationships = lambda tag, _types=None: relations.get(tag, {})
        catalogue = importer.import_catalogues()
    finally:
        importer._e621_page = original_page
        importer._get = original_get
        importer._e621_relationships = original_relationships

    mammal = catalogue["boards"]["e621"]["tag_group:species"]["Mammal"]
    feline = mammal["feline"]
    assert feline["lynx"]["bobcat"]["__tag__"] == "bobcat"
    assert feline["miracinonyx"]["__tag__"] == "miracinonyx"
    implied_feline = mammal["felid"]["feline"]
    genus = implied_feline["caracal_(genus)"]
    assert genus["african_golden_cat"]["__tag__"] == "african_golden_cat"
    assert genus["caracal"]["__tag__"] == "caracal"
    felis = implied_feline["felis"]
    assert felis["wildcat"]["african_wildcat"]["__tag__"] == "african_wildcat"
    assert felis["wildcat"]["european_wildcat"]["__tag__"] == "european_wildcat"
    assert felis["domestic_cat"]["maine_coon"]["__tag__"] == "maine_coon"
    assert "domestic_cat" not in felis["wildcat"]
    assert catalogue["metadata"]["e621"]["feline"]["aliases"] == ["catgirl"]


def test_heading_anchor_and_suffix_are_removed():
    assert _clean_heading("[[Amphibian]]: [#amphibian] [[#top|^]]") == "Amphibian"


def test_gelbooru_only_reads_the_wiki_content_cell():
    source = """
<a href='?page=wiki&s=list&search=outside'>outside</a>
<td><h2 style='display: inline;'>Now Viewing: tag_group:dogs</h2><br>
<b>- Dog breeds -</b><br>
<a href='?page=wiki&s=list&search=Beagle'>Beagle</a></td>
"""
    tree, definition = parse_gelbooru_group(source)
    assert tree["Dog breeds"]["Beagle"]["__tag__"] == "Beagle"
    assert "tag_group:dogs" in definition


def test_gelbooru_intro_and_final_page_links_are_not_imported_as_tags():
    source = """
<td><h2 style='display: inline;'>Now Viewing: some_tag</h2><br>
An explanation with <a href='?page=wiki&s=list&search=unrelated'>a wiki link</a>.<br>
<img src='/images/example.png'><br>
<b>Other Wiki Information</b><br>
<a href='?page=wiki&s=list&search=Tag_group:Example'>Tag_group:Example</a></td>
"""
    tree, _definition = parse_gelbooru_group(source)
    assert tree == {}


def test_gelbooru_stars_create_parent_child_relations():
    source = """
<td><h2 style='display: inline;'>Now Viewing: tag_group:attire</h2><br>
<b><i>- Hats and Headgear -</i></b><br>
<a href='?page=wiki&s=list&search=baseball_cap'>baseball_cap</a><br>
* <a href='?page=wiki&s=list&search=visor_cap'>visor_cap</a><br>
<a href='?page=wiki&s=list&search=chef_hat'>chef_hat</a><br>
* <a href='?page=wiki&s=list&search=toque_blanche'>toque_blanche</a><br>
* <a href='?page=wiki&s=list&search=flat_top_chef_hat'>flat_top_chef_hat</a></td>
"""
    tree, _definition = parse_gelbooru_group(source)
    hats = tree["Hats and Headgear"]
    assert hats["baseball_cap"]["visor_cap"]["__tag__"] == "visor_cap"
    assert hats["chef_hat"]["toque_blanche"]["__tag__"] == "toque_blanche"
    assert hats["chef_hat"]["flat_top_chef_hat"]["__tag__"] == "flat_top_chef_hat"


def test_gelbooru_dash_and_colon_create_child_relations():
    source = """
<td><h2>Now Viewing: plant</h2><br><b>Types:</b><br>
<a href='?page=wiki&s=list&search=bush'>bush</a><br>
— <a href='?page=wiki&s=list&search=rose_bush'>rose_bush</a><br>
<a href='?page=wiki&s=list&search=roots'>roots</a><br>
: <a href='?page=wiki&s=list&search=aerial_root'>aerial_root</a></td>
"""
    tree, _definition = parse_gelbooru_group(source)
    assert tree["Types"]["bush"]["rose_bush"]["__tag__"] == "rose_bush"
    assert tree["Types"]["roots"]["aerial_root"]["__tag__"] == "aerial_root"


def test_pasted_rifle_list_infers_nested_and_sibling_categories():
    source = """
[Rifle](https://gelbooru.com/index.php?page=wiki&s=list&search=Rifle):
Bolt-action:
[Ai_Arctic_Warfare](https://gelbooru.com/index.php?page=wiki&s=list&search=Ai_Arctic_Warfare)
[Arisaka](https://gelbooru.com/index.php?page=wiki&s=list&search=Arisaka)
Semi-automatic:
[Armalite_AR-7_Explorer](https://gelbooru.com/index.php?page=wiki&s=list&search=Armalite_AR-7_Explorer)
"""
    tree = parse_pasted_tag_list(source)
    rifle = tree["Rifle"]
    assert rifle["Bolt-action"]["Ai_Arctic_Warfare"]["__tag__"] == "Ai_Arctic_Warfare"
    assert rifle["Bolt-action"]["Arisaka"]["__tag__"] == "Arisaka"
    assert rifle["Semi-automatic"]["Armalite_AR-7_Explorer"]["__tag__"] == "Armalite_AR-7_Explorer"


def test_pasted_tabs_define_arbitrary_child_depth():
    tree = parse_pasted_tag_list(
        "Tag\n\tEnfant_1\n\tEnfant_2\n\t\tEnfant3_1\nTag2\n\tEnfant2_1\n"
    )
    assert tree["Tag"]["Enfant_1"]["__tag__"] == "Enfant_1"
    assert tree["Tag"]["Enfant_2"]["Enfant3_1"]["__tag__"] == "Enfant3_1"
    assert tree["Tag2"]["Enfant2_1"]["__tag__"] == "Enfant2_1"


def test_pasted_tag_with_compact_slash_is_a_valid_parent_node():
    tree = parse_pasted_tag_list(
        "series\n"
        "\tFate/Stay_Night\n"
        "\t\tSaber\n"
    )
    fate = tree["series"]["Fate/Stay_Night"]
    assert fate["__tag__"] == "Fate/Stay_Night"
    assert fate["Saber"]["__tag__"] == "Saber"


def test_long_unindented_labels_close_the_previous_tab_parent():
    tree = parse_pasted_tag_list(
        "major changes\n"
        "\tanimalization\n"
        "changes of the whole attire\n"
        "\tadapted_costume\n"
        "changes of body parts other than hair and eyes\n"
        "\talternate_breast_size\n"
    )
    assert tree["major changes"]["animalization"]["__tag__"] == "animalization"
    assert "adapted_costume" not in tree["major changes"]
    assert tree["changes of the whole attire"]["adapted_costume"]["__tag__"] == "adapted_costume"
    body = tree["changes of body parts other than hair and eyes"]
    assert body["alternate_breast_size"]["__tag__"] == "alternate_breast_size"


def test_symbol_list_strips_annotations_and_accepts_symbolic_tags_and_bbcode():
    tree = parse_pasted_tag_list(
        "symbols\n"
        "\tAnimals\n"
        "\t\tBat_symbol\n"
        "\tGeometric shapes\n"
        "\t\tCircle // ○\n"
        "\t\tPentagram (5 points)\n"
        "\tSymbols of emotions\n"
        "\t\t^^^\n"
        "\t\t+++\n"
        "\tCurrency symbols\n"
        "\t\t$ / Dollar sign\n"
        "\tGenders / Astronomical symbols\n"
        "\t\tMercury_symbol // ☿\n"
        "\tJapanese symbols (religious, icons)\n"
        "\t\tmagatama\n"
        "\tSymbols of specific series/mangas/games/etc.\n"
        "\t\t[b]Blazblue\n"
        "\t\t\tBlazblue_insignia\n"
    )
    symbols = tree["symbols"]
    assert symbols["Animals"]["Bat_symbol"]["__tag__"] == "Bat_symbol"
    assert symbols["Geometric shapes"]["Circle"]["__tag__"] == "Circle"
    assert symbols["Geometric shapes"]["Pentagram"]["__tag__"] == "Pentagram"
    assert symbols["Symbols of emotions"]["^^^"]["__tag__"] == "^^^"
    assert symbols["Symbols of emotions"]["+++"]["__tag__"] == "+++"
    assert symbols["Currency symbols"]["$"]["__tag__"] == "$"
    genders = symbols["Genders / Astronomical symbols"]
    assert genders["Mercury_symbol"]["__tag__"] == "Mercury_symbol"
    japanese = symbols["Japanese symbols (religious, icons)"]
    assert japanese["magatama"]["__tag__"] == "magatama"
    series = symbols["Symbols of specific series/mangas/games/etc."]
    assert series["Blazblue"]["Blazblue_insignia"]["__tag__"] == "Blazblue_insignia"


def test_preview_reports_indent_jumps_and_tree_size():
    tree, audit = analyze_pasted_tag_list(
        "Root\n\tGroup\n\t\t\ttoo_deep\nSibling\n\tchild\n"
    )
    assert "Root" in tree
    assert audit["nonempty_lines"] == 5
    assert audit["node_count"] == 5
    assert audit["max_depth"] == 3
    assert audit["jumps"] == [
        {"line": 3, "from_depth": 1, "to_depth": 3, "text": "too_deep"}
    ]


def test_merge_preserves_manual_tree_and_local_exclusions():
    document = {
        "boards": {"e621": {"Manuel": ["mine"]}},
        "excluded_imported_tags": {"e621": ["deleted"]},
    }
    imported = {
        "boards": {"e621": {"Groupe": ["kept", "deleted"]}},
        "metadata": {"e621": {}},
        "sources": [],
    }
    summary = merge_catalogues(document, imported)
    assert document["boards"]["e621"]["Manuel"] == ["mine"]
    assert document["boards"]["e621"]["Groupe"] == ["kept"]
    assert summary["total"] == 2


def test_merge_preserves_manual_tags_inside_refreshed_wiki_group():
    document = {
        "boards": {
            "gelbooru": {
                "Weapons": {
                    "Firearms": {
                        "__tags__": ["manual_firearm"],
                        "Rifle": {"__manual__": True, "Bolt-action": ["Arisaka"]},
                    }
                }
            }
        },
        "excluded_imported_tags": {},
    }
    imported = {
        "boards": {"gelbooru": {"Weapons": {"Firearms": {"pistol": {"__tag__": "pistol"}}}}},
        "metadata": {"gelbooru": {}},
        "sources": [],
    }
    merge_catalogues(document, imported)
    firearms = document["boards"]["gelbooru"]["Weapons"]["Firearms"]
    assert firearms["__tags__"] == ["manual_firearm"]
    assert firearms["Rifle"]["Bolt-action"] == ["Arisaka"]
    assert firearms["pistol"]["__tag__"] == "pistol"
