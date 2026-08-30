from __future__ import annotations

from pathlib import Path

from booruflow.application.auto_organize import (
    AnalysisCancelled,
    AutoOrganizer,
    SystemicApiError,
    apply_plans,
    load_rules,
    rule_inventory,
    rule_node_to_dict,
    validate_batch,
    validation_summary,
)
from booruflow.domain.auto_organize import (
    FilePlan,
    OrganizeMode,
    OrganizeRule,
    PlanStatus,
    PostMetadata,
    RuleEngine,
    RuleNode,
    canonical_filename,
)
from booruflow.domain.image_analysis import parse_booru_filename
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache
from booruflow.infrastructure.post_metadata_client import (
    FetchFailure,
    MetadataFetchError,
    PostNotFoundError,
)

NAME="anonymous - 9490613 - sensitive - 0f58173673bf35ef9e0fa7966ea18761.jpg"
DEFAULT_RULES=Path(__file__).resolve().parents[2]/"resources"/"auto_organize_rules.json"

def meta(**kwargs):
    values={"site":"gelbooru","post_id":"9490613","tags":("blue_hair",),"artists":("ma_d_k_89",),"rating":"sensitive","md5":"0f58173673bf35ef9e0fa7966ea18761"}
    values.update(kwargs); return PostMetadata(**values)

def test_standard_filename_and_known_artist_rename():
    parsed=parse_booru_filename(Path(NAME)); assert parsed and parsed.post_id=="9490613"
    assert canonical_filename(parsed,meta(),".jpg").startswith("ma_d_k_89 - 9490613")

def test_cache_hit_and_miss(tmp_path):
    cache=PostMetadataCache(tmp_path/"cache.sqlite")
    assert cache.get("gelbooru","9490613") is None
    cache.put(meta()); loaded=cache.get("gelbooru","9490613",30)
    assert loaded and loaded.artists==("ma_d_k_89",) and "blue_hair" in loaded.tags
    cache.close()

def test_priority_tags_species_copyright_artist_and_internal_levels():
    rules=RuleEngine((
        OrganizeRule("weapons","Tags",240,("gelbooru",),("sword",),"Tags/Weapons"),
        OrganizeRule("professions","Tags",230,("gelbooru",),("knight",),"Tags/Professions"),
        OrganizeRule("races","Tags",220,("gelbooru",),("elf",),"Tags/Races"),
        OrganizeRule("sexual","Tags",210,("gelbooru",),("bondage",),"Tags/Sexual Themes"),
        OrganizeRule("relations","Tags",200,("gelbooru",),("couple",),"Tags/Relations"),))
    md=meta(tags=("sword","knight","elf","bondage","couple"),species=("wolf",),copyrights=("work",),artists=("artist",))
    assert rules.decide(md).winner=="relations"
    assert RuleEngine(load_rules(DEFAULT_RULES)).decide(meta(tags=(),species=("wolf",),copyrights=("work",),artists=("artist",))).winner=="species"

def test_dedicated_triggers_and_boys_ambiguity():
    engine=RuleEngine(load_rules(DEFAULT_RULES))
    gel=engine.decide(meta(tags=("child","witch")))
    assert gel.route=="Tags C&L" and gel.winner=="witch"
    assert gel.destination=="Tags C&L (gelbooru)/Professions/witch"
    e621=engine.decide(meta(site="e621",tags=("young","sword"),artists=("artist",)))
    assert e621.route=="Tags Y&L" and e621.destination.startswith("Tags Y&L (e621)/")
    result=engine.decide(meta(tags=("1boy","1girl","shota","femdom")))
    assert result.ambiguous and result.winner=="boys_review"

def test_equal_priority_is_ambiguous():
    engine=RuleEngine((OrganizeRule("a","Tags",200,("gelbooru",),("x",),"A"),OrganizeRule("b","Tags",200,("gelbooru",),("y",),"B")))
    assert engine.decide(meta(tags=("x","y"))).ambiguous

def _organizer(tmp_path, metadata=None, fetch_error=None):
    cache=PostMetadataCache(tmp_path/"cache.sqlite")
    def fetch(_site,_post):
        if fetch_error: raise fetch_error
        return metadata or meta()
    return AutoOrganizer(cache,fetch,RuleEngine(()),tmp_path/"dest"),cache

def test_refresh_only_renames_but_never_moves(tmp_path):
    folder=tmp_path/"Tags (gelbooru)"; folder.mkdir(); source=folder/NAME; source.write_bytes(b"x")
    organizer,cache=_organizer(tmp_path)
    plan=organizer.plan_file(source,OrganizeMode.REFRESH_ONLY,use_cache=False)
    assert plan.status is PlanStatus.RENAME and plan.destination.parent==source.parent
    malicious=FilePlan(source,mode=OrganizeMode.REFRESH_ONLY,destination=tmp_path/"elsewhere"/source.name,status=PlanStatus.MOVE,source_size=1,source_mtime_ns=source.stat().st_mtime_ns)
    assert apply_plans([malicious])["failed"]==1 and source.exists()
    cache.close()

def test_not_found_and_dry_run_no_filesystem_change(tmp_path):
    folder=tmp_path/"Tags (gelbooru)"; folder.mkdir(); source=folder/NAME; source.write_bytes(b"x")
    organizer,cache=_organizer(tmp_path,fetch_error=PostNotFoundError("9490613"))
    plan=organizer.plan_file(source,OrganizeMode.ORGANIZE,use_cache=False)
    assert plan.status is PlanStatus.NOT_FOUND and source.exists()
    cache.close()

def test_collision_real_apply_and_continue_after_error(tmp_path):
    source1=tmp_path/"one.jpg"; source2=tmp_path/"two.jpg"; source1.write_bytes(b"1"); source2.write_bytes(b"2")
    target=tmp_path/"out"/"same.jpg"
    p1=FilePlan(source1,destination=target,status=PlanStatus.MOVE,source_size=1,source_mtime_ns=source1.stat().st_mtime_ns)
    p2=FilePlan(source2,destination=target,status=PlanStatus.MOVE,source_size=1,source_mtime_ns=source2.stat().st_mtime_ns)
    validate_batch([p1,p2]); assert p1.status is PlanStatus.AMBIGUOUS and source1.exists()
    good=FilePlan(source1,destination=tmp_path/"out"/"one.jpg",status=PlanStatus.MOVE,source_size=1,source_mtime_ns=source1.stat().st_mtime_ns)
    bad=FilePlan(source2,destination=tmp_path/"out"/"two.jpg",status=PlanStatus.MOVE,source_size=999,source_mtime_ns=source2.stat().st_mtime_ns)
    result=apply_plans([bad,good]); assert result=={"applied":1,"unchanged":0,"failed":1,"skipped":0}
    assert good.destination.exists() and source2.exists()

def test_validation_report():
    plans=[FilePlan(Path("a"),status=PlanStatus.UNCHANGED),FilePlan(Path("b"),status=PlanStatus.MOVE),FilePlan(Path("c"),status=PlanStatus.AMBIGUOUS)]
    summary=validation_summary(plans)
    assert {key:summary[key] for key in ("analyzed","exact","divergences","ambiguous","errors")}=={"analyzed":3,"exact":1,"divergences":1,"ambiguous":1,"errors":0}

def _hierarchy(first="professions"):
    profession=RuleNode("professions","Professions","branch",children=(
        RuleNode("nurse","nurse","rule","Tags/Professions/nurse",("nurse",)),
        RuleNode("doctor","doctor","rule","Tags/Professions/doctor",("doctor",)),))
    weapons=RuleNode("weapons","Weapons","branch",children=(
        RuleNode("pistol","pistol","rule","Tags/Weapons/Firearms/pistol",("pistol",)),))
    return (profession,weapons) if first=="professions" else (weapons,profession)

def test_hierarchical_parent_order_precedes_child_order_and_can_change_winner():
    metadata=meta(tags=("doctor","pistol"))
    assert RuleEngine(_hierarchy()).decide(metadata).winner=="doctor"
    assert RuleEngine(_hierarchy("weapons")).decide(metadata).winner=="pistol"

def test_sibling_order_changes_terminal_winner():
    metadata=meta(tags=("nurse","doctor")); roots=_hierarchy()
    assert RuleEngine(roots).decide(metadata).winner=="nurse"
    reversed_profession=RuleNode("professions","Professions","branch",children=tuple(reversed(roots[0].children)))
    assert RuleEngine((reversed_profession,roots[1])).decide(metadata).winner=="doctor"

def test_unordered_siblings_remain_ambiguous():
    root=RuleNode("candidates","Candidats historiques","branch",ordered=False,children=(
        RuleNode("a","a","rule","A",("a",)),RuleNode("b","b","rule","B",("b",))))
    assert RuleEngine((root,)).decide(meta(tags=("a","b"))).ambiguous

def test_override_persistence_and_reset(tmp_path):
    import json
    override=tmp_path/"override.json"; roots=list(load_rules(DEFAULT_RULES)); roots[1],roots[2]=roots[2],roots[1]
    override.write_text(json.dumps({"version":2,"roots":[rule_node_to_dict(n) for n in roots]}),encoding="utf-8")
    assert load_rules(DEFAULT_RULES,override)[1].node_id=="species"
    override.write_text("{}",encoding="utf-8"); assert load_rules(DEFAULT_RULES,override)[1].node_id=="tags"

def test_default_tree_contains_effective_terminal_destinations():
    roots=load_rules(DEFAULT_RULES); tags=next(node for node in roots if node.node_id=="tags")
    def flatten(nodes):
        return [node for value in nodes for node in (value,*flatten(value.children))]
    nodes={node.node_id:node for node in flatten(tags.children)}
    assert nodes["brother_and_sister"].destination=="Tags/Relations/brother_and_sister"
    assert nodes["hitachi_magic_wand"].destination.endswith("Vibrators/hitachi_magic_wand")
    assert nodes["assault_rifle"].tags==("assault_rifle",)

def test_races_contains_validated_simple_leaves_and_explicit_subbranches():
    roots=load_rules(DEFAULT_RULES); tags=next(node for node in roots if node.node_id=="tags")
    races=next(node for node in tags.children if node.node_id=="races")
    def flatten(nodes): return [node for value in nodes for node in (value,*flatten(value.children))]
    nodes={node.node_id:node for node in flatten(races.children)}
    expected={"android","angel","black_sclera","black_skin","blue_skin","centauroid","cyborg",
              "dark_skinned_female","dark_elf","demon_girl","doll_joints","elf","fairy",
              "mecha_musume","monster_girl","orc","purple_skin","vampire","yandere"}
    assert expected <= nodes.keys()
    assert nodes["black_sclera"].destination=="Tags/Races/black_sclera"
    assert nodes["oni"].destination=="Tags/Races/demon_girl/oni"
    assert nodes["succubus"].destination=="Tags/Races/demon_girl/succubus"
    combined={"black_sclera black_skin","dark-skinned_female dark_elf elf doll_joints",
              "dark-skinned_female elf","dark-skinned_female purple_skin","monster_girl blue_skin"}
    assert not combined.intersection({node.label for node in nodes.values()})
    assert not any(" " in node.destination.rsplit("/",1)[-1] for node in nodes.values() if node.destination)

def test_races_priority_and_old_override_merge_respect_user_order(tmp_path):
    import json
    override=tmp_path/"override.json"
    override.write_text(json.dumps({"roots":[{"id":"tags","children":[{"id":"races","children":[
        {"id":"blue_skin"},{"id":"black_skin"}]}]}]}),encoding="utf-8")
    roots=load_rules(DEFAULT_RULES,override); tags=next(node for node in roots if node.node_id=="tags")
    races=next(node for node in tags.children if node.node_id=="races")
    assert [node.node_id for node in races.children[:2]]==["blue_skin","black_skin"]
    decision=RuleEngine(roots).decide(meta(tags=("black_skin","blue_skin")))
    assert decision.winner=="blue_skin" and decision.destination=="Tags/Races/blue_skin"
    assert any(node.node_id=="black_sclera" for node in races.children)

def test_old_override_order_preserves_new_default_leaves(tmp_path):
    import json
    override=tmp_path/"override.json"; override.write_text(json.dumps({"roots":[{"id":"tags","children":[{"id":"weapons","children":[]}]}]}),encoding="utf-8")
    tags=load_rules(DEFAULT_RULES,override)[0]
    assert tags.node_id=="tags" and tags.children[0].node_id=="weapons" and tags.children[0].children

def test_canonical_inventory_is_deep_complete_and_site_aware():
    roots=load_rules(DEFAULT_RULES); inventory=rule_inventory(roots)
    expected={"Relations":6,"Sexual Themes":90,"Races":26,"Professions":45,"Weapons":30,
              "Animal Ears":35,"General":50,"Piercings":24,"Styles vestimentaires":75,"HairStyles":30}
    assert all(inventory["branches"][name]>=minimum for name,minimum in expected.items())
    assert inventory["tags_total"]>=470 and inventory["gelbooru"]>inventory["shared"]
    assert inventory["e621"]>inventory["shared"]

def test_canonical_tree_contains_requested_historical_leaves_and_no_compound_folder_rule():
    roots=load_rules(DEFAULT_RULES); tags=next(node for node in roots if node.node_id=="tags")
    def flatten(node): return [node,*[nested for child in node.children for nested in flatten(child)]]
    by_branch={branch.label:flatten(branch) for branch in tags.children}
    expected={"Relations":{"couple","twins"},"Sexual Themes":{"ball_gag","rape","you_gonna_get_raped"},
              "Races":{"dwarf","spider_girl","zombie"},"Professions":{"alchemist","witch","wizard"},
              "Weapons":{"anti-tank_rifle","pistol_sword","spiked_club"},
              "Animal Ears":{"aardwolf_ears","cat_ears","wolf_ears"},
              "Piercings":{"ear_piercing","nipple_piercing"},
              "Styles vestimentaires":{"bikini","school_uniform","wetsuit"},
              "HairStyles":{"absurdly_long_hair","hair_over_face","two-tone_hair"}}
    for branch,labels in expected.items(): assert labels <= {node.label for node in by_branch[branch]}
    forbidden={"dark-skinned_female dark_elf elf doll_joints","monster_girl blue_skin",
               "girls_und_panzer kantai_collection","hisakawa_hayate hisakawa_nagi"}
    assert not forbidden.intersection({node.label for nodes in by_branch.values() for node in nodes})

def test_routing_continues_to_tags_and_never_ends_at_route_root():
    engine=RuleEngine(load_rules(DEFAULT_RULES))
    result=engine.decide(meta(tags=("loli","bikini"),copyrights=("work",),characters=("hero",)))
    assert not result.ambiguous and result.route=="Tags C&L"
    assert result.classification=="tags" and result.destination.endswith("Styles vestimentaires/Swimsuits/bikini")
    assert result.destination!="Tags C&L (gelbooru)"

def test_copyright_character_fallback_accepts_all_multiple_value_shapes():
    engine=RuleEngine(load_rules(DEFAULT_RULES))
    cases=((('youjo_senki',),('tanya_degurechaff',),"Copyright/youjo_senki/tanya_degurechaff"),
           (('work',),('a','b'),"Copyright/work/a b"),
           (('work_a','work_b'),('hero',),"Copyright/work_a work_b/hero"),
           (('work_a','work_b'),('hero_a','hero_b'),"Copyright/work_a work_b/hero_a hero_b"))
    for copyrights,characters,destination in cases:
        result=engine.decide(meta(tags=(),artists=(),copyrights=copyrights,characters=characters))
        assert not result.ambiguous and result.destination==destination
        assert result.classification=="copyright" and result.fallback.startswith("Copyright / ")

def test_branch_priority_can_choose_race_then_profession_for_same_tags():
    roots=list(load_rules(DEFAULT_RULES)); tags=next(node for node in roots if node.node_id=="tags")
    children={node.node_id:node for node in tags.children}; metadata=meta(tags=("witch","elf","sword"))
    races_first=RuleNode(tags.node_id,tags.label,tags.kind,children=tuple(children[name] for name in ("races","professions","weapons")))
    professions_first=RuleNode(tags.node_id,tags.label,tags.kind,children=tuple(children[name] for name in ("professions","races","weapons")))
    assert RuleEngine((races_first,)).decide(metadata).winner=="elf"
    assert RuleEngine((professions_first,)).decide(metadata).winner=="witch"

def test_old_override_cannot_revert_route_or_copyright_structure(tmp_path):
    import json
    override=tmp_path/"override.json"
    override.write_text(json.dumps({"roots":[{"id":"dedicated","children":[{"id":"gelbooru_cl","kind":"rule"}]},
        {"id":"copyright","destination":"Copyright/{value}","special":""}]}),encoding="utf-8")
    roots=load_rules(DEFAULT_RULES,override)
    route=next(node for node in roots[0].children if node.node_id=="gelbooru_cl")
    copyright_node=next(node for node in roots if node.node_id=="copyright")
    assert route.kind=="route" and copyright_node.special=="copyright_character"

def test_old_general_alias_and_custom_override_rules_are_preserved(tmp_path):
    import json
    override=tmp_path/"override.json"
    override.write_text(json.dumps({"roots":[{"id":"tags","children":[{"id":"other_tags","children":[
        {"id":"my_custom","label":"my_custom","kind":"rule","tags":["my_custom"],
         "destination":"Tags/my_custom"}]}]}]}),encoding="utf-8")
    roots=load_rules(DEFAULT_RULES,override); tags=next(node for node in roots if node.node_id=="tags")
    general=next(node for node in tags.children if node.node_id=="general")
    assert any(node.node_id=="my_custom" for node in general.children)

def test_known_post_4824095_uses_copyright_then_character_without_ambiguity():
    tags=("1girl","absurdres","blue_eyes","brown_gloves","brown_hair","closed_mouth",
          "female_focus","gloves","green_jacket","hair_between_eyes","hand_up","highres",
          "hiranko","jacket","long_hair","long_sleeves","looking_down","military_jacket",
          "ponytail","signature","solo","tanya_degurechaff","upper_body","youjo_senki")
    result=RuleEngine(load_rules(DEFAULT_RULES)).decide(meta(tags=tags,artists=("hiranko",),
        copyrights=("youjo_senki",),characters=("tanya_degurechaff",)))
    assert result.destination=="Copyright/youjo_senki/tanya_degurechaff"
    assert result.classification=="copyright" and not result.ambiguous and not result.has_tag_match

def test_known_post_6773033_does_not_invent_black_dress_rule():
    engine=RuleEngine(load_rules(DEFAULT_RULES))
    result=engine.decide(meta(tags=("black_dress","jewelry"),artists=("gishiki_(gshk)",),copyrights=("original",)))
    assert result.winner=="jewelry" and result.destination=="Tags/Styles vestimentaires/jewelry"
    tags_root=next(node for node in engine.rules if node.node_id=="tags")
    def flatten(node): return [node,*[nested for child in node.children for nested in flatten(child)]]
    assert "black_dress" not in {tag for node in flatten(tags_root) for tag in node.tags}

def test_historical_extractor_skips_compound_queries(tmp_path):
    import json

    from tools.rebuild_auto_organize_rules import collect
    monitors={"monitors":[
        {"query":{"tags":["witch"]},"sites":["gelbooru.com"],
         "filenameOverride":"Tags (%websitename%)/Professions/%search%/file.%ext%"},
        {"query":{"tags":["dark_elf","elf","doll_joints"]},"sites":["gelbooru.com"],
         "filenameOverride":"Tags (%websitename%)/Races/%search%/file.%ext%"}]}
    source=tmp_path/"monitors.json"; source.write_text(json.dumps(monitors),encoding="utf-8")
    leaves,skipped,_counts=collect(source)
    assert any(leaf.family=="Professions" and leaf.label=="witch" for leaf in leaves)
    assert not any(leaf.label=="dark_elf elf doll_joints" for leaf in leaves)
    assert skipped[0]["reason"]=="compound_query"

def test_cooperative_cancellation_during_file_loop_keeps_sources(tmp_path):
    folder=tmp_path/"Tags (gelbooru)"; folder.mkdir()
    for post_id in range(1,5): (folder/f"anonymous - {post_id} - sensitive - 0f58173673bf35ef9e0fa7966ea18761.jpg").write_bytes(b"x")
    organizer,cache=_organizer(tmp_path); checks=0
    def cancel():
        nonlocal checks; checks+=1; return checks>12
    try: organizer.plan((folder,),OrganizeMode.REFRESH_ONLY,True,use_cache=False,cancel_check=cancel)
    except AnalysisCancelled as exc: assert len(exc.plans)<4
    else: raise AssertionError("cancellation was not observed")
    assert len(list(folder.glob("*.jpg")))==4; cache.close()

def test_ten_identical_infrastructure_errors_stop_analysis_early(tmp_path):
    folder=tmp_path/"Tags (gelbooru)"; folder.mkdir()
    for post_id in range(1,13):(folder/f"anonymous - {post_id} - sensitive - 0f58173673bf35ef9e0fa7966ea18761.jpg").write_bytes(b"x")
    cache=PostMetadataCache(tmp_path/"systemic.sqlite"); calls=[]; reports=[]
    def fail(_site,post_id):
        calls.append(post_id); raise MetadataFetchError(FetchFailure("gelbooru","remote_fetch","https://gelbooru.com/index.php?page=dapi","HTTPError","HTTP Error 401: Unauthorized",401))
    organizer=AutoOrganizer(cache,fail,RuleEngine(()),tmp_path/"out",reports.append)
    try: organizer.plan((folder,),OrganizeMode.ORGANIZE,True,use_cache=False)
    except SystemicApiError as exc: assert len(exc.plans)==10
    else: raise AssertionError("systemic failure was not detected")
    assert len(calls)==10 and len(reports)==10 and all(path.exists() for path in folder.glob("*.jpg")); cache.close()
