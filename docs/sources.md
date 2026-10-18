# Sources and references

Everything the project states about Finnish practice, the Tyks and Varha environment, the
statistics and the regulation was read from the documents below during JunctionX Turku 2026 - TYKS Laboratories
(16 to 18 October 2026). Items marked *assumed* in the deck are those for which no public
page states the claim. No patient-level data was read from any source; the only data in the
project is the synthetic dataset supplied with the challenge.

## 1. Challenge material

- The challenge brief (”Antibiotic efficacy indicator, AEI”) and the judging criteria, supplied by
  TYKS Laboratories for JunctionX Turku 2026 - TYKS Laboratories.
- The synthetic dataset and its README: [`data/README.md`](../data/README.md) (structure, the
  first-isolate and 30-isolate conventions, antibiotic codes; its own sources are THL Finres 2023,
  EUCAST Expected Resistant Phenotypes v1.2 and the ECDC EARS-Net and WHO GLASS protocols).
- ECDC, EARS-Net data: https://www.ecdc.europa.eu/en/about-us/networks/disease-networks-and-laboratory-networks/ears-net-data
- THL, antimicrobial resistance in Finland: https://thl.fi/en/data-and-statistics/infectious-diseases/antimicrobial-resistance

## 2. Methods and statistics

- CLSI M39, *Analysis and Presentation of Cumulative Antimicrobial Susceptibility Test Data*,
  5th ed. (first isolate per patient, at least 30 isolates): https://clsi.org/standards/products/microbiology/documents/m39/
- Moehring et al., *J Clin Microbiol* 2015;53:2977 (adherence to M39 conventions):
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4540907
- Truong et al., *JAC-Antimicrobial Resistance* 2021;3:dlab060 (the 30-isolate rule in practice):
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8210055
- EUCAST clinical breakpoints and Expected Resistant Phenotypes (the intrinsic-resistance pairs in
  `build/config.py`): https://www.eucast.org/clinical_breakpoints
- WHONET, including its SaTScan cluster detection, as the reference tool for laboratory AMR
  surveillance: https://whonet.org
- Wilson score interval (Wilson 1927) for the 95 % interval on every percentage; a one-sided
  exact binomial test for the rise detector; the Poisson tail for the cluster detector.
  Implemented with SciPy (`scipy.stats`). What each method is and why it was chosen:
  [`build/README.md`, Methods](../build/README.md#methods-what-is-computed-and-why).
- Brown, Cai and DasGupta, *Statistical Science* 2001;16:101 (interval estimation for a binomial
  proportion; the case for Wilson over Wald at small n): https://doi.org/10.1214/ss/1009213286
- Wilson, *J Am Stat Assoc* 1927;22:209 (the score interval): https://doi.org/10.1080/01621459.1927.10502953
- Newcombe, *Stat Med* 1998;17:857 (seven interval methods for a single proportion compared;
  Wilson recommended): https://pubmed.ncbi.nlm.nih.gov/9595616
- Jeffreys, *Proc R Soc A* 1946;186:453 (the Jeffreys prior; the 0.5 pseudocount on the baseline
  rate): https://doi.org/10.1098/rspa.1946.0056
- Farrington, Andrews, Beale and Catchpole, *J R Stat Soc A* 1996;159:547 (a current count
  against a baseline from past windows; the design both detectors follow):
  https://doi.org/10.2307/2983331
- Kulldorff, *Commun Stat Theory Methods* 1997;26:1481 (the spatial scan statistic behind
  SaTScan): https://doi.org/10.1080/03610929708831995
- Stelling et al., *Epidemiol Infect* 2010;138:873 (WHONET and SaTScan for laboratory cluster
  detection): https://pmc.ncbi.nlm.nih.gov/articles/PMC4093803
- Gelman et al., *Bayesian Data Analysis*, 3rd ed., 2013, chapter 5 (hierarchical Beta-binomial
  shrinkage, the roadmap trend indicator): http://www.stat.columbia.edu/~gelman/book/
- Hundepool et al., *Statistical Disclosure Control*, Wiley 2012 (small-cell suppression):
  https://doi.org/10.1002/9781118348239
- Findata, guidance on anonymisation of aggregate output from secondary-use data (threshold of
  five per cell): https://findata.fi/en/
- Tilastokeskus, concept ”luottamusväli” (Finnish statistical terminology):
  https://stat.fi/meta/kas/luottamusvali.html

## 3. Finnish surveillance reports and clinical vocabulary

The Finnish labels in the page follow what these documents print; the vocabulary work compared
eighteen concepts against them (herkkyystilasto, % R, 1 kanta/potilas, näytetyyppi, märkä,
sairaalasyntyinen / avohoitosyntyinen, tarkastelujakso, epidemiaepäily, luontainen resistenssi,
herkkyystulkintaraja, empiirinen mikrobilääkehoito, infektioiden torjunta, mikrobilääkehoidon
ohjaus, kattavuus).

- Tyks, ”Antibioottiresistenssitilanne Varsinais-Suomessa” 2021 to 2024, Kliininen mikrobiologia:
  https://www.tyks.fi/ammattilaiselle/kliinisen-mikrobiologian-raportit-ammattilaisille
  (2024 report: https://v-shva.ims.fi/spring/public/4bd918e7-6f63-4688-83ca-d37e1626885e/fi)
- HUS Diagnostiikkakeskus, ”HUSRES, Mikrobiologian herkkyystilastot” (WHONET, EUCAST, % R):
  https://diagnostiikka.hus.fi/documents/d/guest/mikrobiologian-herkkyystilastot-2022-helsinki-ja-uusimaa
- THL, Finres 2023, ”Bakteerien mikrobilääkeresistenssi Suomessa”, Työpaperi 63/2024:
  https://www.julkari.fi/server/api/core/bitstreams/af81c9a6-9a89-4873-9844-5e13d3332fb5/content
  and the Finres database: https://www3.thl.fi/finres
- THL, Ohjaus 22/2017, ”Ohje moniresistenttien mikrobien tartunnantorjunnasta” (MDR phenotype names,
  epidemiaepäily): http://urn.fi/URN:ISBN:978-952-302-943-9
- KTL, C11/2005, SIRO seurantakäsikirja ”Veriviljelypositiiviset sairaalainfektiot” (hospital- and
  community-onset definitions): https://www.julkari.fi/bitstream/handle/10024/78375/2005c11.pdf
- THL, hoitoon liittyvät infektiot: https://thl.fi/data-ja-tilastot/infektiotaudit/hoitoon-liittyvat-infektiot
- HUS, Mikrobilääkehoito-opas 2026: https://www.hus.fi/sites/default/files/2026-02/mikrobilaakehoito-opas.pdf
- Tyks, Satasairaala and Vaasa, ”Mikrobilääkesuositus” (13 April 2026; its susceptibility table is
  from 2021, the staleness the tool removes):
  https://hoito-ohjeet.fi/fi/Ohjepankki/YHTEINEN/Mikrobil%C3%A4%C3%A4kesuositus.pdf
- NordLab, ”Mikrobilääkeresistenssitilanne 2019 to 2022” and Lappi 2023 (nordlab.fi), and the
  Pirkanmaa Antibioottiopas 2025, read for vocabulary comparison.
- Duodecim, ”Sairaalasyntyinen sepsis” (duo60204) and the HUS stewardship group (duo16464), for the
  onset and stewardship terms: https://www.duodecimlehti.fi

## 4. The Tyks and Varha environment

Used for the production appendix in the deck: what exists today, what is assumed.

- Tyks Laboratoriot for professionals, clinical microbiology pages:
  https://www.tyks.fi/ammattilaiselle/ammattilaisten-palvelut-ja-ohjeet/tyks-laboratoriot-ammattilaiselle and
  https://www.tyks.fi/ammattilaiselle/kliinisen-mikrobiologian-ohjeet-ammattilaisille
- The laboratory system (Mylab Weblab Clinical / My+): https://mylabgroup.com/fi/asiakastarinat/tyks-laboratoriot/,
  https://www.tyks.fi/ammattilaiselle/farmakogeneettiset-tutkimukset (HL7 into Weblab, result
  display in the patient record), examination handbook https://webohjekirja.mylabservices.fi/TYKS/
- The patient record system change (Omni360 from December 2026):
  https://www.varha.fi/fi/ajankohtaista/tyks-siirtyy-kayttamaan-uutta-potilastietojarjestelmaa-joulukuussa-2026,
  https://www.sttinfo.fi/tiedote/72377126/tyks-laajentaa-digitaalisia-palvelujaan-varha-sovelluksessa?lang=fi,
  product page https://www.cgi.com/fi/fi/tuoteratkaisut/omni360-asiakas-ja-potilastietojarjestelma
- The Varha data lake and laboratory results in it: Auria Tietopalvelu (archived page)
  https://web.archive.org/web/20250202013928/https://www.auria.fi/tietopalvelu/meista/,
  the lake dataset record ”Tyksin laboratoriotulokset” with its requesting-unit hierarchy
  https://aineistokatalogi.fi/catalog/studies/88e89e7f-7aa6-4221-918a-1da56bbfb71c,
  STM, Tietoallasselvitys 4 March 2026:
  https://stm.fi/documents/1271139/253648856/4.3.2026_Tietoallasselvitys.pdf/e78578fe-56c1-1385-98d1-a1ac8d7cf6a6?t=1773738225734
- Register research and permits at Varha: https://www.varha.fi/fi/tietoa-meista/tieteellinen-tutkimus/tutkijan-ohjeet/rekisteritutkimus
- Infection surveillance and management reporting (SAI register, ”johdon tietopöytä”): VSSHP
  laadunhallintasuunnitelma 2022, p. 29:
  https://www.tyks.fi/sites/default/files/2022-10/Varsinais-Suomen%20sairaanhoitopiirin%20laadunhallintasuunnitelma.pdf
- Interfaces: Mylab on FHIR https://fhir.fi/en/demo2023/mylab/, Finnish SMART App Launch profile
  https://hl7.fi/fhir/finnish-smart/
- Tyks in Finres and the FiRe laboratory network: Finres 2023 p. 6 (above) and the archived THL
  list https://web.archive.org/web/20250317181220id_/https://thl.fi/aiheet/infektiotaudit-ja-rokotukset/seurantajarjestelmat-ja-rekisterit/mikrobilaakeresistenssin-seuranta/mikrobilaakeresistenssin-tutkimusryhma-fire/fire-laboratoriot

## 5. Regulation

The position taken: a surveillance tool on the same footing as the annual report, lawful under
the secondary-use act's knowledge-management section, not a medical device; a patient-specific
recommendation would be one and is kept apart.

- Act on the Secondary Use of Health and Social Data 552/2019, section 41 (knowledge management):
  https://www.finlex.fi/fi/lainsaadanto/2019/552#chp_4__sec_41 (statute PDF:
  https://www.finlex.fi/api/media/statute/695851/mainPdf/main.pdf); Kuntaliitto briefing on
  section 41: https://www.kuntaliitto.fi/sites/default/files/media/file/Sorvettula%20Johanna.pdf
- Act on the Processing of Client Data in Healthcare and Social Welfare 703/2023:
  https://www.finlex.fi/fi/laki/alkup/2023/20230703
- Act on the Status and Rights of Patients 785/1992: https://www.finlex.fi/fi/laki/ajantasa/1992/19920785
- Communicable Diseases Act 1227/2016, section 17: https://www.finlex.fi/fi/laki/ajantasa/2016/20161227
- GDPR 2016/679: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- Medical Device Regulation 2017/745: https://eur-lex.europa.eu/eli/reg/2017/745/oj, with MDCG
  2019-11 rev. 1 (June 2025) on software qualification:
  https://health.ec.europa.eu/document/download/b45335c5-1679-4c71-a91c-fc7a4d37f12b_en?filename=mdcg_2019_11_en.pdf
- AI Act 2024/1689: https://eur-lex.europa.eu/eli/reg/2024/1689/oj; Commission guidelines on the
  AI-system definition: https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai
- European Health Data Space 2025/327: https://eur-lex.europa.eu/eli/reg/2025/327/oj
- Fimea (in-house devices): https://fimea.fi

## 6. Models, libraries and assets

- Laya decision model, Convai Innovations, Apache 2.0: https://huggingface.co/convaiinnovations/laya
  (`pip install laya`); overviews https://www.llmreference.com/model-family/laya,
  https://www.i-scoop.eu/laya-system-1-decision-model/, https://www.eesel.ai/blog/laya-ai
  (vendor-run benchmarks; our figures come from our own labelled set, see `ai/README.md`)
- llama.cpp, used for the comparison profiles and for the paraphrases in the training set:
  https://github.com/ggml-org/llama.cpp; Qwen3-30B-A3B-Instruct-2507 and Qwen3.6-27B (Qwen team,
  Alibaba) as the compared local language models: https://huggingface.co/Qwen
- Python: pandas, NumPy, SciPy, FastAPI, Uvicorn, httpx, PyTorch 2.7.1; environment management
  with uv (https://docs.astral.sh/uv/)
- Fonts: IBM Plex Sans and IBM Plex Mono (page), Source Serif 4 (deck), via Google Fonts
- Logos: Tyks and Varha marks from the Varha material bank (varha.kuvat.fi), see
  `web/assets/brand/README.md`
