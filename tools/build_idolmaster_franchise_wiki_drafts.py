from __future__ import annotations

import json, re, sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/databases/g_tags_260810.db'; OUT=ROOT/'var/wiki_drafts'/'Idolmaster'

GROUPS={
"2005 - 765PRO ALLSTARS":["amami_haruka","kisaragi_chihaya","hagiwara_yukiho","takatsuki_yayoi","akizuki_ritsuko","miura_azusa","minase_iori","kikuchi_makoto","futami_ami","futami_mami","hoshii_miki","ganaha_hibiki","shijou_takane","otonashi_kotori","producer_(idolmaster)"],
"2009 - Dearly Stars":["hidaka_ai","mizutani_eri","akizuki_ryou"],
"2011 - Cinderella Girls (principal and voiced cast selection)":["shimamura_uzuki","shibuya_rin","honda_mio","futaba_anzu","maekawa_miku","kanzaki_ranko","jougasaki_mika","jougasaki_rika","moroboshi_kirari","koshimizu_sachiko","takagaki_kaede","nitta_minami","anastasia_(idolmaster)","sagisawa_fumika","ichinose_shiki","miyamoto_frederica","hayami_kanade","shiomi_shuuko","sato_shin","morikubo_nono","hoshi_shouko","koshimizu_sachiko","tachibana_arisu","sakurai_momoka","akagi_miria","ryuuzaki_kaoru","sasaki_chie","ichihara_nina","matoba_risa","yuuki_haru","koga_koharu"],
"2013 - Million Live!":["kasuga_mirai","mogami_shizuka","ibuki_tsubasa","tanaka_kotoha","shimabara_elena","tokoro_megumi","tokugawa_matsuri","hakozaki_serika","nonohara_akane","mochizuki_anna","nanao_yuriko","baba_konomi","fukuda_noriko","yokoyama_nao","nikaidou_chizuru","toyokawa_fuka","makabe_mizuki","nagayoshi_subaru","suou_momoko","handa_roco","miyao_miya","momose_rio","maihama_ayumu","kinoshita_hinata","yabuki_kana","kitazawa_shiho","tenkuubashi_tomoka","nakatani_iku","ogami_tamaki","emily_stewart","matsuda_arisa","kousaka_umi","takayama_sayoko","satake_minako","kitakami_reika","shinomiya_karen","julia_(idolmaster)","sakuramori_kaori","shiraishi_tsumugi"],
"2014 - SideM":["amagase_touma","ijuuin_hokuto","mitarai_shouta","tendou_teru","sakuraba_kaoru","kashiwagi_tsubasa","takajou_kyouji","pierre_(idolmaster)","watanabe_minori","iseya_shiki","akiyama_hayato","wakazato_haruna","fuyumi_jun","sakaki_natsuki","aoi_yuusuke","aoi_kyousuke","hazama_michio","maita_rui","yamashita_jirou","hanamura_shoma","kiyosumi_kuro","nekoyanagi_kirio","akuno_hideo","kimura_ryuu","shingen_seiji","akai_suzaku","kurono_genbu","asselin_bb_ii","mizushima_saki","kamiya_yukihiro","shinonome_souichirou","uzuki_makio","tsuzuki_kei","kagura_rei","taiga_takeru","kizaki_ren","enjoji_michiru","okamura_nao","tachibana_shirou","himeno_kanon","akizuki_ryou","kabuto_daigo","tsukumo_kazuki","kuzunoha_amehiko","kitamura_sora","koron_chris","amamine_shuu","mayumi_eishin","hanazono_momohito"],
"2018 - Shiny Colors: illumination STARS":["sakuragi_mano","kazano_hiori","hachimiya_meguru"],
"2018 - Shiny Colors: L'Antica":["tsukioka_kogane","tanaka_mamimi","shirase_sakuya","mitsumine_yuika","yuukoku_kiriko"],
"2018 - Shiny Colors: Houkago Climax Girls":["komiya_kaho","sonoda_chiyoko","saijou_juri","morino_rinze","arisugawa_natsuha"],
"2018 - Shiny Colors: ALSTROEMERIA":["osaki_amana","osaki_tenka","kuwayama_chiyuki"],
"2019-2023 - Later Shiny Colors units":["serizawa_asahi","mayuzumi_fuyuko","izumi_mei","asakura_toru","higuchi_madoka","fukumaru_koito","ichikawa_hinana","nanakusa_nichika","aketa_mikoto","ikaruga_luca","ikuta_haruki","suzuki_hana"],
"2023 - vα-liv":["kamizuru_cosmo","tomori_manaka","letora_(idolmaster)"],
"2024 - Gakuen Idolmaster":["hanami_saki","tsukimura_temari","fujita_kotone","arimura_mao","katsuragi_lilja","kuramoto_china","shiun_sumika","shinosawa_hiro","himesaki_rinami","hanami_ume","hataya_misuzu","juo_sena"],
}

BRANCHES=["idolmaster_(classic)","idolmaster_dearly_stars","idolmaster_cinderella_girls","idolmaster_million_live!","idolmaster_side-m","idolmaster_shiny_colors","idolmaster_va-liv","gakuen_idolmaster"]

def resolve(con, tag):
 r=con.execute('select name,category from tags where name=?',(tag,)).fetchone()
 if r:return r[0]
 for c in (tag+'_(idolmaster)', tag+'_(gakuen_idolmaster)'):
  r=con.execute('select name,category from tags where name=?',(c,)).fetchone()
  if r:return r[0]
 return None

def compact(s):
 s=re.sub(r'\n+(\[h[1-6]\])',r'\1',s);return re.sub(r'(\[/h[1-6]\])\n+',r'\1',s)

def main():
 con=sqlite3.connect(DB); resolved={}; missing=[]
 for title,tags in GROUPS.items():
  resolved[title]=[]
  for tag in tags:
   found=resolve(con,tag)
   if found and found not in resolved[title]:resolved[title].append(found)
   elif not found:missing.append((title,tag))
 lines=["[b]The Idolmaster[/b] is a multimedia idol-raising and rhythm-game franchise created by Namco, now Bandai Namco Entertainment. The original arcade game was released on July 26, 2005 and casts the player as a producer at 765 Production who trains aspiring idols.","","As the earlier version of this article explained, the arcade game stored each player's progress on a magnetic-stripe card. Series creator Akihiro Ishihara designed its limited play sessions and persistent idols to encourage an emotional attachment between player and character. The unexpected arcade and Xbox 360 success grew into games, music, radio programs, manga, anime and live concerts performed by the voice cast.","","Most branches follow a producer and a different talent agency or school. Their casts can meet in crossover games and anniversary projects, but a tag for one branch should not automatically be added to characters or costumes from another.","[h2]Main branches in chronological order[/h2]","* [[idolmaster_(classic)]] - the 765 Production continuity begun by the 2005 arcade game; also covers [[idolmaster_1]], [[idolmaster_2]], [[idolmaster_sp]], [[idolmaster_one_for_all]], [[idolmaster_platinum_stars]] and [[idolmaster_stella_stage]].","* [[idolmaster_dearly_stars]] - 2009 Nintendo DS game centered on 876 Production.","* [[idolmaster_cinderella_girls]] - branch launched in 2011; related tags include [[idolmaster_cinderella_girls_starlight_stage]] and [[idolmaster_cinderella_girls_u149]].","* [[idolmaster_million_live!]] - branch launched in 2013 around the 765 Pro Live Theater; see [[idolmaster_million_live!_theater_days]] and [[idolmaster_million_live!_(anime)]].","* [[idolmaster_side-m]] - male-idol branch launched in 2014; see [[idolmaster_side-m_live_on_stage!]] and [[idolmaster_side-m_growing_stars]].","* [[idolmaster_shiny_colors]] - 283 Production branch launched in 2018; see [[idolmaster_shiny_colors_song_for_prism]].","* [[idolmaster_va-liv]] - virtual-idol project launched in 2023.","* [[gakuen_idolmaster]] - school-centered branch launched in 2024.","[h2]Cross-brand games[/h2]","* [[idolmaster_poplinks]]","* [[idolmaster_starlit_season]]","* [[idolmaster_tours]]","[h2]Anime and major adaptations[/h2]","* [[idolmaster_xenoglossia]] - 2007 alternate-universe mecha adaptation with a separate continuity and different voice cast.","* [[idolmaster_movie]] - film continuation of the 2011 television anime.","* [[idolmaster_cinderella_girls_u149]]","* [[idolmaster_million_live!_(anime)]]","* The Shiny Colors television anime normally uses [[idolmaster_shiny_colors]] unless a more specific established tag applies.","* [[idolmaster.kr]] - Korean live-action adaptation with an original cast.","[h2]Character index[/h2]","See [[List_of_Idolmaster_characters]] for characters grouped by the project that introduced them, in chronological branch order.","[h2]Related tags[/h2]","* [[bandai_namco_entertainment]]","* [[namco]]","[h2]External sources[/h2]","* Official franchise portal: https://idolmaster-official.jp/","* Official 20th anniversary portal: https://idolmaster-official.jp/20th_anniversary/","* Official Million Live! idol directory: https://millionlive-theaterdays.idolmaster-official.jp/idol/","* Official vα-liv portal: https://idolmaster-official.jp/va-liv"]
 list_lines=["[b]About this list:[/b]","This index groups major Idolmaster characters by the game or project in which their branch was introduced. Dates refer to the branch's debut, not every individual character's first playable appearance.","","The list uses established Gelbooru tags from the local database. It focuses on named idols and central production characters; costume variants, card names, song-specific designs and minor NPCs are excluded."]
 for title,tags in resolved.items():list_lines.append(f"[h2]{title}[/h2]"+'\n'.join(f'* [[{t}]]' for t in tags))
 list_lines += ["[h2]See also[/h2]","* [[idolmaster]]"]
 pages={'idolmaster':lines,'List_of_Idolmaster_characters':list_lines}
 OUT.mkdir(parents=True,exist_ok=True)
 for tag,src in pages.items():
  (OUT/f'{tag}.json').write_text(json.dumps({'tag':tag,'template':'copyright' if tag=='idolmaster' else 'general','source':compact('\n'.join(src)),'updated_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('resolved',sum(map(len,resolved.values())),'missing',missing)

if __name__=='__main__':main()
