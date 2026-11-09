This is a reverse engineering project mainly focusing on the Suunto Ambit 3 watch and its variations.
A lot of work has been done, so please check sommet .md files 

Operation wise it is very similar to it's predecessors Suunto Ambit 2 and Ambit 1.
Here is the codenames for the watches of that era
Product	Internal codename	
Ambit	Bluebird	
Ambit2	Duck	
Ambit2 S	Colibri	
Ambit2 R	Greentit	
Ambit3 Peak	Emu	
Ambit3 Sport	Finch	
Ambit3 Run	Ibisbill	
Ambit3 Vertical	Kaka	
Traverse	Jabiru	
Traverse Alpha	Loon	
Kailash	Hoopoe	

This is a 2015 black and white watch, with very simple hardware capabilities.It doesn't have whirst heart rate sensor, having only temperature sensor, barometer, altimeter, compass. Athlete info is inputed via suunto link and described in steps_welness_data_andre.md

Giving this information prioritize searching for pre made made formulas/know sports theories than complex calculations

Historical data:
Suunto ambit and traverse where operated via moveslink2 (pc and mac) and movescount app (android and ios), till 2020-21. Data was retrieved from movescount website and sent to the watches and vice versa.
After 2021 suunto closed movescount. So don't ask for dumps using these 2 pieces of software.
This 2 pieces of software are present in our assets folder.

In 2021 movescount closed and all data is now on suunto app (android and ios).
This app only has limited functionality for ambit and traverse series (sync orbital data and activities).
Routes and POIs present in Suunto app, need to be toggled "used on the watch" and then be synced via cable with suuntolink
described here
https://www.suunto.com/fr-fr/Assistance/faq-articles/transition/comment-utiliser-les-services-de-suunto-avec-ambit3/

https://www.suunto.com/fr-fr/Assistance/faq-articles/suuntolink/nouvelle-mise-a-jour-suuntolink-maintenant-compatible-avec-les-gammes-de-montres-ambit-et-traverse/

https://www.suunto.com/fr-fr/Assistance/faq-articles/transition/comment-creer-et-modifier-les-modes-sportifs-pour-mon-ambit-123-ou-traverse/

A dump of suunto app instalation folders is presents in assets folder
Suunto app apks are also present in assets folder


During this migration the following features were lost for the ambit line up
- Sports mode customization via bluetooth (ambit 3 and traverse/traverse alpha only)
- Training program creation (planned moves)
- Route sync via bluetooth (cloud to watch) (ambit 3 and traverse/traverse alpha only)
- POI sync via bluetooth (cloud to watch) (ambit 3 and traverse/traverse alpha only)
- Personal information including body metrics synced and changed via bluetooth (ambit 3 and traverse/traverse alpha only)


These features were present on the movescount app (Apk on our assets folder) and some on moveslink2 app (instalation folder on our assets folder).

Bear in mind movescount and moveslink also supported next gen watches such as spartan range wich was a different platform/hardware

Bear in mind that suunto app supports fully the next gen suunto 5,9,9 peak, 9 peak pro, race, race s, till the actual models.

Suuntolink supports watches from suunto ambit 1 to suunto 9, full list here
https://www.suunto.com/fr-fr/Assistance/assistance-logicielle/suuntolink/

suunto app supports fhe following watches via bluetooth:

Suunto Run
Suunto Vertical 2
Suunto Race 2
Suunto Race
Race S
Suunto Vertical
Suunto 9 Peak Pro, 9 Peak, 9 Baro, and standard 9
Suunto 5 Peak and standard 5
Suunto 3 (and 3 Fitness)
Suunto 7 (Wear OS)
Suunto Spartan series (Ultra, Sport, Trainer)
Suunto Ambit3 series (Peak, Sport, Run)
Suunto Traverse and Traverse Alpha

and other diving watches which are not meaninful for our project.

Assume that ambit series being legacy devices their functions can be very different from the newer ones, and that suunto app and suuntolink were build to limited compatibility within theses watches (within what was possible given the new platform, and being an outdated watch so limited resources allocated)

For the functions missing or half baked, worth to check the implementation of movescount app (in our assets) for bluetooth related functions and moveslink2 (in our assets) for cable operated sync. always having in mind the movescount server is dead. so no capture and the server links are irrelevant.

Openambit project added linux compatibility to some of the ambit watches via cable, although not fully reverse engineered and still linked to old movescount
https://github.com/openambitproject/openambit/

Opensports sync does the same for android https://github.com/guiguoz/opensportsync

In the past there was ambit connect and ambit sync for android, present in our assets folder as Ambit+Connect_1.6.3_APKPure.apk and idv.markkuo.ambitsync_9_Version 1.4.1 (9)_2022.apk , that also offered some type of syncronization via otg for android for ambit watches.

User guides are present in assets/manuals and should be consulted for understanding features/functions of the watch

For everything related to apps/intervals check SuuntoAppZoneDeveloperManual.pdf
present in assets/manuals and assets/Intervals

marguslt also reverse engineered a couple of ambit/movescount features:
https://gist.github.com/marguslt/a79ea204f99b45ab015b6ed1ff7529a4#file-mc-workout-termlog-txt

https://gist.github.com/marguslt/4ad715ae43475cd90031c29f9ff1f039
https://github.com/marguslt/openmoves


Giving all this, whatever functions/features can be related/ported to the ambit 1,2 or traverse family let me know, so in the future we can contribute to opensportsync and openambit.
Sommet has as first and main aim the bluetooth syncronization of an offline gpx to the watch, but if possible could evolve to support other features by bluetooth. 


