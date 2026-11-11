// Bot Discord de Discord Transcript : rejoint un salon vocal et envoie au serveur local
// une piste audio par participant, pour une transcription « qui dit quoi » sans
// mélange des voix, même quand plusieurs personnes parlent en même temps.
//
// Il se pilote depuis la page web (via le serveur, qui appelle l'API de contrôle
// locale ci-dessous) ou, en secours, par les commandes /enregistrer et /stop.

import { timingSafeEqual } from "node:crypto";
import http from "node:http";
import { ChannelType, Client, Events, GatewayIntentBits, MessageFlags } from "discord.js";
import {
  EndBehaviorType,
  VoiceConnectionStatus,
  entersState,
  joinVoiceChannel,
} from "@discordjs/voice";
import prism from "prism-media";

// Lancé par le serveur (app/bots.py), un processus par bot configuré dans les réglages
const TOKEN = process.env.DISCORD_TOKEN;
const BOT_ID = process.env.BOT_ID;
const SERVER = process.env.TRANSCRIPT_SERVER || "http://127.0.0.1:8010";
const ACCESS_KEY = process.env.TRANSCRIPT_ACCESS_KEY || "";
const CONTROL_PORT = Number(process.env.BOT_CONTROL_PORT);
// Secret tiré par le serveur à chaque lancement : seule l'app peut piloter le bot (une page web
// ouverte dans le navigateur ne peut pas envoyer cet en-tête au port local)
const CONTROL_KEY = Buffer.from(process.env.BOT_CONTROL_KEY || "");
const MAX_BODY = 64 * 1024;
const TAG = `[bot ${process.env.BOT_NAME}]`; // préfixe du journal (plusieurs bots peuvent tourner)

// Audio décodé : PCM 16 bits mono 48 kHz
const SAMPLE_RATE = 48000;
const BYTES_PER_SEC = SAMPLE_RATE * 2;
// Fin d'une prise de parole après SILENCE_MS sans paquet audio. Les prises de parole
// longues sont découpées sur un passage calme après SOFT_SEC, ou de force à MAX_SEC.
const SILENCE_MS = 1000, SOFT_SEC = 20, MAX_SEC = 30, MIN_SEC = 0.4, QUIET_RMS = 300;
// Arrêt automatique quand il ne reste plus personne dans le salon
const EMPTY_CHANNEL_MS = 60_000;

if (!TOKEN || !BOT_ID || !CONTROL_PORT || !CONTROL_KEY.length) {
  console.error(`${TAG} lancé sans DISCORD_TOKEN / BOT_ID / BOT_CONTROL_PORT / BOT_CONTROL_KEY : il est démarré par le serveur.`);
  process.exit(1);
}

const COMMANDS = [
  { name: "enregistrer", description: "Enregistre et transcrit le salon vocal où tu te trouves" },
  { name: "stop", description: "Arrête l'enregistrement en cours" },
];

const recordings = new Map(); // guildId -> Recording

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function rms(pcm) {
  let sum = 0;
  const n = pcm.length >> 1;
  for (let i = 0; i < n; i++) {
    const v = pcm.readInt16LE(i * 2);
    sum += v * v;
  }
  return n ? Math.sqrt(sum / n) : 0;
}

async function api(path, options = {}) {
  const headers = { ...options.headers, ...(ACCESS_KEY && { "X-Access-Key": ACCESS_KEY }) };
  const res = await fetch(SERVER + path, { ...options, headers });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

class Recording {
  constructor(channel, connection, sessionId) {
    this.channel = channel;
    this.guild = channel.guild;
    this.connection = connection;
    this.sessionId = sessionId;
    this.startedAt = Date.now();
    this.speakers = new Map(); // userId -> prise de parole en cours
    this.inflight = new Set();
    this.stopped = false;
    this.emptyTimer = null;
    connection.receiver.speaking.on("start", (userId) => this.listen(userId));
  }

  listen(userId) {
    if (this.stopped || this.speakers.has(userId)) return;
    const member = this.guild.members.cache.get(userId);
    if (member?.user.bot) return;

    const opus = this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_MS },
    });
    const decoder = new prism.opus.Decoder({ rate: SAMPLE_RATE, channels: 1, frameSize: 960 });
    const take = { chunks: [], bytes: 0, startMs: 0 };
    this.speakers.set(userId, take);

    decoder.on("data", (pcm) => {
      if (!take.bytes) take.startMs = Date.now() - this.startedAt - pcm.length / BYTES_PER_SEC * 1000;
      take.chunks.push(pcm);
      take.bytes += pcm.length;
      const sec = take.bytes / BYTES_PER_SEC;
      if (sec >= MAX_SEC || (sec >= SOFT_SEC && rms(pcm) < QUIET_RMS)) this.flush(userId, take);
    });
    let ended = false;
    const end = (err) => {
      if (ended) return;
      ended = true;
      if (err) console.warn(`${TAG} flux audio de ${userId} interrompu : ${err.message}`);
      if (this.speakers.get(userId) === take) this.speakers.delete(userId);
      this.flush(userId, take);
    };
    decoder.once("end", () => end());
    decoder.once("error", end);
    opus.once("error", end);
    opus.pipe(decoder);
  }

  flush(userId, take) {
    if (!take.bytes) return;
    const pcm = Buffer.concat(take.chunks);
    const startMs = Math.max(0, Math.round(take.startMs));
    take.chunks = [];
    take.bytes = 0;
    if (pcm.length < MIN_SEC * BYTES_PER_SEC) return; // bruit bref : rien d'exploitable
    const job = this.send(userId, pcm, startMs)
      .catch((e) => console.error(`${TAG} envoi au serveur impossible : ${e.message}`))
      .finally(() => this.inflight.delete(job));
    this.inflight.add(job);
  }

  async send(userId, pcm, startMs) {
    const member = this.guild.members.cache.get(userId)
      ?? (await this.guild.members.fetch(userId).catch(() => null));
    const form = new FormData();
    form.append("audio", new Blob([pcm]), "take.s16");
    form.append("sample_rate", String(SAMPLE_RATE));
    form.append("user_id", userId);
    form.append("speaker", member?.displayName ?? `Participant ${userId.slice(-4)}`);
    form.append("start_ms", String(startMs));
    await api(`/api/discord/sessions/${this.sessionId}/utterances`, { method: "POST", body: form });
  }

  async stop() {
    if (this.stopped) return;
    this.stopped = true;
    clearTimeout(this.emptyTimer);
    recordings.delete(this.guild.id);
    for (const [userId, take] of this.speakers) this.flush(userId, take);
    this.speakers.clear();
    this.connection.destroy();
    await Promise.allSettled([...this.inflight]);
    await api(`/api/discord/sessions/${this.sessionId}/stop`, { method: "POST" })
      .catch((e) => console.error(`${TAG} clôture de la session impossible : ${e.message}`));
    await this.channel.send("⏹️ **Enregistrement terminé.** La transcription est dans Discord Transcript.").catch(() => {});
    console.log(`${TAG} enregistrement arrêté dans #${this.channel.name}`);
  }

  // Appelé au démarrage et à chaque arrivée / départ dans le salon
  reportParticipants(names) {
    if (this.stopped) return;
    api(`/api/discord/sessions/${this.sessionId}/participants`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    }).catch((e) => console.error(`${TAG} liste des participants non envoyée : ${e.message}`));
  }

  checkEmpty() {
    const humans = this.channel.members.filter((m) => !m.user.bot);
    this.reportParticipants([...humans.values()].map((m) => m.displayName));
    clearTimeout(this.emptyTimer);
    if (!humans.size) {
      this.emptyTimer = setTimeout(() => {
        console.log(`${TAG} salon #${this.channel.name} vide — arrêt de l'enregistrement`);
        this.stop();
      }, EMPTY_CHANNEL_MS);
    }
  }
}

// profileId : profil (onglet) qui lance l'enregistrement ; null pour /enregistrer dans Discord
async function startRecording(channel, profileId = null) {
  if (recordings.has(channel.guild.id)) {
    throw new HttpError(409, "Un enregistrement est déjà en cours sur ce serveur.");
  }

  let session;
  try {
    session = await api("/api/discord/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_id: BOT_ID, profile_id: profileId ?? "",
        guild: channel.guild.name, guild_id: channel.guild.id, channel: channel.name, channel_id: channel.id,
      }),
    });
  } catch (e) {
    throw new HttpError(502, `Impossible de joindre Discord Transcript (${SERVER}) : ${e.message}`);
  }

  const connection = joinVoiceChannel({
    channelId: channel.id,
    guildId: channel.guild.id,
    adapterCreator: channel.guild.voiceAdapterCreator,
    selfDeaf: false, // indispensable pour recevoir l'audio
    selfMute: true,
  });
  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
  } catch {
    connection.destroy();
    await api(`/api/discord/sessions/${session.id}/stop`, { method: "POST" }).catch(() => {});
    throw new HttpError(502, "Connexion au salon vocal impossible (vérifie la permission « Se connecter » du bot).");
  }

  const recording = new Recording(channel, connection, session.id);
  recordings.set(channel.guild.id, recording);
  connection.on(VoiceConnectionStatus.Disconnected, async () => {
    try {
      // Changement de salon ou reconnexion en cours : on laisse la connexion se rétablir
      await Promise.race([
        entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
        entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
      ]);
    } catch {
      recording.stop();
    }
  });
  recording.checkEmpty();

  // Prévient les participants dans le chat du salon vocal
  await channel.send(`🔴 **Enregistrement en cours** dans 🔊 ${channel.name} : les prises de parole sont transcrites par Discord Transcript.`)
    .catch(() => {});
  console.log(`${TAG} enregistrement démarré dans #${channel.name} (${channel.guild.name})`);
  return recording;
}

// --- Contrôle depuis la page web (via le serveur ; écoute uniquement en local) ----
// guildId : limite la liste à un serveur (celui choisi dans les réglages de l'app)
async function listChannels(guildId) {
  const channels = [];
  for (const guild of client.guilds.cache.values()) {
    if (guildId && guild.id !== guildId) continue;
    const voice = guild.channels.cache
      .filter((c) => c.type === ChannelType.GuildVoice && c.joinable)
      .sort((a, b) => a.rawPosition - b.rawPosition);
    for (const channel of voice.values()) {
      const states = guild.voiceStates.cache.filter((v) => v.channelId === channel.id && v.id !== client.user.id);
      const people = [];
      for (const state of states.values()) {
        const member = state.member ?? (await guild.members.fetch(state.id).catch(() => null));
        if (!member?.user.bot) people.push(member?.displayName ?? "Participant");
      }
      channels.push({
        id: channel.id,
        name: channel.name,
        guild: guild.name,
        guild_id: guild.id,
        people,
        recording: recordings.get(guild.id)?.channel.id === channel.id,
      });
    }
  }
  return channels;
}

function authorized(req) {
  const given = Buffer.from(req.headers["x-control-key"] || "");
  return given.length === CONTROL_KEY.length && timingSafeEqual(given, CONTROL_KEY);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > MAX_BODY) reject(new HttpError(413, "Requête trop volumineuse."));
    });
    req.on("end", () => {
      try { resolve(body ? JSON.parse(body) : {}); } catch (e) { reject(new HttpError(400, "JSON invalide")); }
    });
    req.on("error", reject);
  });
}

const control = http.createServer(async (req, res) => {
  const send = (status, body) => {
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(body));
  };
  try {
    if (!authorized(req)) throw new HttpError(403, "Accès refusé.");
    if (!client.isReady()) throw new HttpError(503, "Le bot Discord est en cours de connexion, réessaie dans un instant.");
    const url = new URL(req.url, "http://localhost");
    if (req.method === "GET" && url.pathname === "/guilds") {
      const guilds = [...client.guilds.cache.values()].map((g) => ({ id: g.id, name: g.name }));
      return send(200, guilds.sort((a, b) => a.name.localeCompare(b.name)));
    }
    if (req.method === "GET" && url.pathname === "/channels") return send(200, await listChannels(url.searchParams.get("guild")));
    if (req.method === "POST" && url.pathname === "/record") {
      const { channel_id: channelId, profile_id: profileId } = await readJson(req);
      const channel = client.channels.cache.get(channelId);
      if (channel?.type !== ChannelType.GuildVoice) throw new HttpError(404, "Salon vocal introuvable.");
      const recording = await startRecording(channel, profileId);
      return send(200, { session_id: recording.sessionId, channel: channel.name });
    }
    if (req.method === "POST" && url.pathname === "/stop") {
      const { session_id: sessionId } = await readJson(req);
      const recording = [...recordings.values()].find((r) => r.sessionId === sessionId);
      if (!recording) throw new HttpError(404, "Aucun enregistrement en cours pour cette réunion.");
      await recording.stop();
      return send(200, { stopped: sessionId });
    }
    send(404, { detail: "Route inconnue." });
  } catch (e) {
    if (!e.status) console.error(TAG, e);
    send(e.status || 500, { detail: e.message });
  }
});

// --- Commandes Discord (secours) ---------------------------------------------
async function onCommand(interaction) {
  if (interaction.commandName === "enregistrer") {
    const channel = interaction.member?.voice?.channel;
    if (!channel) {
      return interaction.reply({ content: "Rejoins d'abord un salon vocal, puis relance `/enregistrer`.", flags: MessageFlags.Ephemeral });
    }
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    await startRecording(channel);
    return interaction.editReply("C'est parti : l'enregistrement a démarré.");
  }
  if (interaction.commandName === "stop") {
    const recording = recordings.get(interaction.guildId);
    if (!recording) {
      return interaction.reply({ content: "Aucun enregistrement en cours.", flags: MessageFlags.Ephemeral });
    }
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    await recording.stop();
    return interaction.editReply("Enregistrement arrêté.");
  }
}

const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates] });

client.once(Events.ClientReady, async (c) => {
  // Commandes déclarées par serveur : disponibles immédiatement (les globales mettent du temps)
  for (const guild of c.guilds.cache.values()) {
    await guild.commands.set(COMMANDS).catch((e) => console.error(`${TAG} commandes non enregistrées sur ${guild.name} : ${e.message}`));
  }
  console.log(`${TAG} connecté en tant que ${c.user.tag} sur ${c.guilds.cache.size} serveur(s)`);
});

client.on(Events.GuildCreate, (guild) => guild.commands.set(COMMANDS).catch(() => {}));

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand() || !interaction.inGuild()) return;
  try {
    await onCommand(interaction);
  } catch (e) {
    if (!e.status) console.error(TAG, e);
    const content = `Erreur : ${e.message}`;
    if (interaction.deferred || interaction.replied) await interaction.editReply(content).catch(() => {});
    else await interaction.reply({ content, flags: MessageFlags.Ephemeral }).catch(() => {});
  }
});

client.on(Events.VoiceStateUpdate, (oldState, newState) => {
  const recording = recordings.get(newState.guild.id);
  if (!recording) return;
  const id = recording.channel.id;
  if (oldState.channelId === id || newState.channelId === id) recording.checkEmpty();
});

async function shutdown() {
  control.close();
  await Promise.allSettled([...recordings.values()].map((r) => r.stop()));
  client.destroy();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

control.listen(CONTROL_PORT, "127.0.0.1", () => console.log(`${TAG} contrôle local sur le port ${CONTROL_PORT}`));
client.login(TOKEN);
