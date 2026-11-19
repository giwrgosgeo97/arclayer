// What the ArcLayer helper knows. Each topic: words people use (q), the answer (a, simple HTML) and
// optional buttons (go: [label, action]). Actions are run by app.js (see HELP_ACTIONS there).
// The helper also searches every setting (label + tip) and every calibration test by itself.
export const HELP = [
  { id: 'what', t: 'What is non-planar printing?',
    q: 'what is non planar nonplanar curved layers how does it work why arc layer explain idea',
    a: `A normal slicer cuts the model into flat layers, so slopes and domes get "stairs". ArcLayer bends the layers:
      the nozzle moves up and down <b>inside</b> each layer and follows the shape. Tops come out smooth, and
      overhangs can be printed without support.`,
    go: [['Show me the intro', 'intro']] },
  { id: 'start', t: 'How do I make my first print?',
    q: 'start begin first print how to use steps quick guide getting started tutorial slice slicing',
    a: `<ol><li><b>Import</b> a model (File → Import, or drag an STL / 3MF onto the plate).</li>
      <li>Pick your <b>printer</b>, <b>filament</b> and <b>process</b> (quality) on the left.</li>
      <li>Choose the curved-layer <b>mode</b> in Process → Non-planar.</li>
      <li>Press <b>Slice plate</b>, then check the <b>Preview</b> (press ▶ Simulate to watch it).</li>
      <li><b>Print plate</b> sends it to your printer, or Export saves the file.</li></ol>`,
    go: [['Open Non-planar settings', 'np']] },
  { id: 'modes', t: 'Conical or "follow the top surface"?',
    q: 'mode conical cone surface follow top wavy which mode choose difference dome rim sculpted',
    a: `<b>Conical</b>: every layer is a cone. Use it for parts that get wider (or narrower) as they go up: the overhang
      prints without support.<br><b>Follow the top surface</b>: the low layers stay flat and the top layers bend to the
      shape of the model's top. Use it for wavy rims, domes and curved tops, which come out smooth instead of stepped.`,
    go: [['Open Non-planar settings', 'np']] },
  { id: 'wavy', t: 'The top still prints flat / not wavy',
    q: 'not wavy straight flat lines top still flat steps stairs surface mode not working',
    a: `Check that the process uses <b>Mode: Follow the top surface</b>. The layers bend over about 2× the
      <b>depth followed</b> (default 8 mm), so the model must be taller than that. If the slope is steeper than your
      toolhead can reach without hitting the print, ArcLayer flattens it: see <i>Top surface: steepest slope</i>
      and Printer → Toolhead clearance. Try the <b>Non-planar test</b> (wavy cup) in Calibration.`,
    go: [['Open Non-planar settings', 'np'], ['Calibration menu', 'calib']] },
  { id: 'collision', t: 'Can the nozzle hit the print?',
    q: 'collision crash hit head toolhead fan duct hit print safety clearance knock',
    a: `Curved layers move the nozzle down next to parts already printed, so the hot end, fan duct or cables could
      touch them. ArcLayer checks this with your printer's <b>Toolhead clearance</b> and flattens slopes that are too
      steep (setting: <i>If the toolhead could hit the print</i>). Always look at the preview, and watch the first
      curved layers.`,
    go: [['Toolhead clearance', 'clearance']] },
  { id: 'calib', t: 'Which calibrations should I do?',
    q: 'calibration calibrate test tower which first order temperature flow pressure advance pa retraction',
    a: `For a new filament, in this order: <b>Temperature tower</b> → <b>Flow rate</b> → <b>Pressure advance</b> →
      <b>Retraction</b>. Then, if you like: Max volumetric speed, First layer, Top surface flow, Ironing, and the
      <b>Non-planar test</b>. Print it, look at it, type the best value in the panel and press <b>Apply</b>: it is
      saved in the filament (or process) preset.`,
    go: [['Calibration menu', 'calib']] },
  { id: 'pa', t: 'Pressure advance (PA / K)',
    q: 'pressure advance pa k value linear advance corners bulge blobs',
    a: `Pressure advance makes the nozzle stop and start oozing on time, so corners are sharp. Print the
      <b>Pressure advance tower</b>: it uses a new K every 1 mm of height. Find the height where the corners look best and
      enter it; ArcLayer turns it into the K value. Bambu printers can also measure it themselves: tick
      <i>Flow dynamics calibration</i> in Send print.`,
    go: [['Calibration menu', 'calib']] },
  { id: 'stringing', t: 'Stringing / hairs between parts',
    q: 'stringing strings hairs oozing ooze wisps retraction',
    a: `Usually too hot or too little retraction. Print the <b>Temperature tower</b> (take the coolest temperature that
      still looks good), then the <b>Retraction</b> test. Dry filament helps a lot, especially PETG and TPU.`,
    go: [['Calibration menu', 'calib']] },
  { id: 'adhesion', t: 'The print comes off the bed / warping',
    q: 'adhesion stick warp warping lift corners come off bed first layer not sticking',
    a: `Clean the plate (dish soap and water for PEI), check the plate type matches the plate you use, and print the
      <b>First layer</b> test to set the first-layer flow. For PETG/ABS raise the bed temperature in the filament
      preset; a brim (Process → Others) helps small or tall parts.`,
    go: [['Calibration menu', 'calib']] },
  { id: 'speed', t: 'Print speed (Silent / Standard / Sport / Ludicrous)',
    q: 'speed fast slow too fast silent standard sport ludicrous quicker time long hours',
    a: `The built-in presets print at about 80% of Bambu Studio's speeds. Change them under Process → Speed. While
      printing, the Device tab has the printer's speed buttons: Silent 50%, Standard 100%, Sport 124%, Ludicrous 166%.
      Speeds are also capped by the filament's <i>max volumetric speed</i>.`,
    go: [['Speed settings', 'speed'], ['Device tab', 'device']] },
  { id: 'connect', t: 'Connect my Bambu printer',
    q: 'connect printer bambu lan ip serial access code developer mode network wifi cannot connect a1 p1s x1',
    a: `Printer settings → <b>Connection</b>: fill in the printer's IP address, serial number and access code (on the
      printer screen: Settings → Network / WLAN). On newer firmware also turn on <b>LAN Only</b> and
      <b>Developer Mode</b>. The PC and printer must be on the same network. Then Device → Connect.`,
    go: [['Connection settings', 'connection'], ['Device tab', 'device']] },
  { id: 'klipper', t: 'Creality / Klipper / OctoPrint / Prusa',
    q: 'creality k1 k2 klipper moonraker fluidd mainsail octoprint prusa prusalink voron sovol elegoo qidi network print',
    a: `Printer settings → Connection → <b>Connection</b>: pick Klipper (Moonraker), OctoPrint or PrusaLink and type the
      printer's address (OctoPrint: also the API key; PrusaLink: the password from the printer screen). Then you can
      upload &amp; print, see progress, pause/stop, temperatures and the camera. Stock Creality K1/K2 firmware may need
      root for Moonraker.`,
    go: [['Connection settings', 'connection']] },
  { id: 'several', t: 'Two or more printers',
    q: 'two printers several multiple printers a1 second printer farm both',
    a: `Make a printer preset for each printer (⚙ → Save as…, e.g. "A1 #2") with its own IP, serial and access code,
      pick it and press Connect. They all stay connected; the bar at the top of the Device tab shows each one and
      switches between them.`,
    go: [['Device tab', 'device']] },
  { id: 'camera', t: 'Camera / Tapo camera',
    q: 'camera tapo webcam rtsp stream video cam my camera zoomed black picture',
    a: `Device → Cameras. <b>Printer camera</b> works by itself once connected. For a <b>Tapo</b>: in the Tapo app open
      the camera → Settings → Advanced Settings → <b>Camera Account</b> and make a user name and password (not your Tapo
      account). Newer firmware: also Me → Tapo Lab → Third-Party Compatibility. Then ⚙ Set up, 🔍 Find, Test, Save.
      ⛶ (or a double-click) shows it full screen.`,
    go: [['Device tab', 'device']] },
  { id: 'live', t: 'Live view',
    q: 'live view watch print progress where is it now current line 3d',
    a: `Device → <b>👁 Live view</b> shows the print in 3D: what is done so far and the line being printed now. The menu
      in the live bar shows a camera next to it (printer camera or your own).`,
    go: [['Device tab', 'device']] },
  { id: 'runout', t: 'When a spool runs out (AMS)',
    q: 'runout run out empty spool next slot ams switch order backup refill',
    a: `Device → AMS lite → <b>When a spool runs out</b>: <i>Pause</i> (Bambu's default), <i>Printer's auto refill</i>
      (same filament, the printer picks the slot) or <i>Switch slots in my order</i>: tap the slots in the order you
      want (e.g. 1 → 3 → 4 → 2) and Save. The app must stay open for "my order".`,
    go: [['Device tab', 'device']] },
  { id: 'multi', t: 'Multicolour / several filaments',
    q: 'multicolor multicolour multi color colours filaments ams slots paint two colors change filament',
    a: `Add filaments with <b>+</b> next to Filament on the left. Then click a part (in the list or on the plate) and
      press 1–4 to give it a filament; Process → Multimaterial can also print walls, infill or support with a
      different one. When sending, choose the AMS slot for each filament. Bambu printers flush into the chute; other
      printers use their own change macro (Creality CFS, Qidi Box, Anycubic ACE) or pause for a manual swap (M600), and
      purge into a <b>prime tower</b>: a small block next to the model (Process → Multimaterial → Prime tower).`,
    go: [['Prime tower settings', 'tower']] },
  { id: 'byobject', t: 'Print one object after another (by object)',
    q: 'print by object one at a time sequence sequential each object separately several objects parts order gantry',
    a: `Process → Others → <b>Print sequence: By object</b>. Each object is printed to the top before the next one starts,
      each with its own curved layers; the lowest goes first, the tallest last. Between objects the nozzle goes up above
      everything first. ArcLayer warns if two objects are closer than the toolhead (about 65 mm on most printers) or if an
      object that isn't the last is taller than the gantry (25 mm on the A1); ⊞ Arrange spaces them for you. Printer settings → Toolhead clearance has
      both numbers.`,
    go: [['Print sequence setting', 'sequence']] },
  { id: 'arrange', t: 'Arrange the objects on the plate (auto arrange)',
    q: 'arrange auto arrange spread space spacing distance objects parts plate layout pack place apart overlap close together',
    a: `<b>⊞ Arrange</b> in the bar above the plate (or the A key) spreads the parts of the model over the plate, biggest
      first, as compact as possible and centred. Printing by layer they get 6 mm between them (plus the brim); printing
      by object they get the toolhead clearance from Printer settings (65 mm on the A1), so the toolhead can't hit a
      finished object. Parts that touch stay together (a multi-colour model stays in one piece), and each group is set
      down on the plate. The objects keep the rotation and scale of the model. If they don't fit, nothing is moved.`,
    go: [['Arrange now', 'arrange'], ['Print sequence setting', 'sequence']] },
  { id: 'report', t: 'Report a problem / a bug',
    q: 'bug report problem crash broken error wrong not working issue feedback send developer contact',
    a: `File → <b>Report a problem…</b>: write what happened. ArcLayer makes a small .zip (what you wrote, the app log,
      the settings in use, a picture of the plate) with your IP addresses, serial numbers, codes, passwords and camera
      logins removed, and opens an email for you to send. Nothing is sent without you.`,
    go: [['Report a problem', 'report']] },
  { id: 'update', t: 'Updates / new version',
    q: 'update new version latest upgrade download newest',
    a: `ArcLayer checks once a day whether a new version is out and shows a bar at the top with a Download button
      (File → Check for updates checks now). Installing a new version keeps your presets and settings.` },
  { id: 'send', t: 'Send to the printer / export',
    q: 'send print upload export gcode 3mf save file sd card usb print plate',
    a: `<b>Print plate</b> (top right) uploads to a connected printer and starts it (Device → Send print has the options:
      bed levelling, timelapse, AMS slots…). Or File → <b>Export G-code</b> / Export plate sliced file to save it, e.g.
      for a USB stick or SD card.`,
    go: [['Device tab', 'device']] },
  { id: 'preview', t: 'Preview colours and simulation',
    q: 'preview colour color by speed flow volumetric temperature layer time simulate simulation play',
    a: `After slicing, the <b>Preview</b> tab shows every line. <i>Colour by</i> switches between line type, speed, volumetric
      flow, line width, layer height, layer time, fan, temperature, acceleration and pressure advance. ▶ <b>Simulate</b> plays the nozzle moving;
      the slider picks a layer.`,
    go: [['Preview', 'preview']] },
  { id: 'sync', t: 'Same settings on two PCs',
    q: 'sync two pcs laptop desktop other computer share settings onedrive google drive dropbox account',
    a: `File → <b>Sync with my other PCs…</b> and pick a folder your cloud app syncs (OneDrive, Google Drive, Dropbox).
      Do the same on the other PC with the same folder. Presets and settings then follow you; no account needed.`,
    go: [['Open sync', 'sync']] },
  { id: 'chooser', t: 'Add a printer or filament to the lists',
    q: 'add printer filament list missing my printer not listed choose show hide brands',
    a: `File → <b>Printers &amp; filaments…</b> (or ☰ next to Printer / Filament) shows over 100 printers and the
      filaments of every brand. Tick the ones you use.`,
    go: [['Printers & filaments', 'chooser']] },
  { id: 'presets', t: 'Change and save settings (presets)',
    q: 'preset save settings change edit custom my own reset default factory modified',
    a: `Click ⚙ next to the printer, filament or process to open its settings. Changed values show ↺ to undo them.
      <b>Save</b> keeps the change in that preset, <b>Save as…</b> makes your own copy. Built-in presets can always go
      back to factory values.` },
  { id: 'tools', t: 'Move, rotate, scale, lay flat, supports',
    q: 'move rotate scale size resize lay on face flat orient place center paint support tools',
    a: `The tool bar on the left of the plate: <b>Move</b>, <b>Rotate</b>, <b>Scale</b>, <b>Lay on face</b> (click the face
      that should touch the bed) and <b>Supports</b> (paint where support is needed). The X / Y / scale boxes above the
      plate take exact numbers.`,
    go: [['Prepare', 'prepare']] },
  { id: 'supports', t: 'Supports',
    q: 'support supports tree overhang hang under bridge',
    a: `Process → <b>Support</b> turns supports on and sets the type and distances. Supports are built on the same curved
      layers as the model, so they meet curved undersides at the right gap. Often the <b>Conical</b> mode prints an
      outward overhang without any support. You can also paint where support goes with the Supports tool.`,
    go: [['Support settings', 'support']] },
  { id: 'install', t: 'Windows says "protected your PC"',
    q: 'windows protected smartscreen install unsafe virus warning signed defender run anyway',
    a: `ArcLayer isn't code-signed yet, so Windows warns about it. Click <b>More info</b>, then <b>Run anyway</b>. It installs
      for your user only (no admin rights) and never sends your data anywhere.` },
  { id: 'rename', t: 'NonPlanar Slicer is now ArcLayer',
    q: 'old name rename renamed arclayer new name previous version gone missing presets after update',
    a: `Same app, new name. On the first start ArcLayer copied your presets, printers, camera and settings from the old
      "NonPlanar Slicer" folder; that old folder was left as it was, as a backup.` },
  { id: 'files', t: 'Where are my files?',
    q: 'where files folder data presets location appdata documents export folder saved',
    a: `Presets, printers and settings: <b>%APPDATA%\\ArcLayer</b>. Exported prints: <b>Documents\\ArcLayer</b>
      (File → Open export folder).` },
  { id: 'shortcuts', t: 'Keyboard shortcuts',
    q: 'keyboard shortcuts keys hotkeys ctrl',
    a: `Ctrl+N new · Ctrl+O open · Ctrl+S save (Ctrl+Shift+S save as) · Ctrl+I import · Ctrl+G export the sliced file.` },
  { id: 'blank', t: 'The 3D view is blank / white',
    q: 'blank white black empty 3d view nothing shows plate gone disappeared',
    a: `The graphics card dropped the 3D view (screen off, PC locked). ArcLayer brings it back by itself when you go to
      Prepare or Preview; if it doesn't, close and reopen the app: your project is kept until you close it.` },
  { id: 'free', t: 'Is it free? Supporting',
    q: 'free price cost pay donate support kofi ko-fi supporter code',
    a: `Yes, free: no ads, no account, no paid version. If it helps you, the <b>Support</b> button (Ko-fi) keeps it going;
      Ko-fi gives you a supporter code that hides the reminder.` },
];

// small talk and words that mean the same thing (both directions are tried)
export const SYNONYMS = {
  colour: 'color', colours: 'color', multicolour: 'multicolor', grey: 'gray',
  nonplanar: 'non planar', curved: 'curve', wave: 'wavy', waves: 'wavy', bend: 'curve',
  stick: 'adhesion', sticking: 'adhesion', warping: 'warp', lifting: 'warp',
  strings: 'stringing', string: 'stringing', ooze: 'stringing',
  quick: 'fast', quicker: 'fast', faster: 'fast', slower: 'slow',
  cam: 'camera', webcam: 'camera', tapo: 'camera',
  wifi: 'connect', lan: 'connect', network: 'connect', ip: 'connect',
  crash: 'collision', hit: 'collision',
  spool: 'runout', empty: 'runout',
  temp: 'temperature', temps: 'temperature', heat: 'temperature',
  pa: 'pressure', k: 'pressure',
  gcode: 'export', usb: 'export', sd: 'export',
  count: 'loop', number: 'loop', walls: 'wall loop', perimeter: 'wall', perimeters: 'wall',
  calibrate: 'calibration', calibrations: 'calibration', test: 'calibration', tower: 'calibration',
};

export const SMALLTALK = [
  [/^(hi|hey|hello|yo|γεια|kalimera)\b/i, 'Hi! Ask me anything about ArcLayer: settings, calibrations, your printer…'],
  [/^(thanks|thank you|thx|ty|ευχαριστ)/i, 'You\'re welcome! 🙂'],
  [/^(who are you|what are you)/i, 'I\'m the ArcLayer helper. I know the app and every setting in it, and I work without internet.'],
];

export const STOP = new Set(('a an the and or of to in on for with my me i is are it its do does how what why when where can ' +
  'could should would will be this that there their your you we our from by at as so if not no yes get got use using want ' +
  'need make print printing printer about any some please help many much').split(' '));
