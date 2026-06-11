import { contextBridge, ipcRenderer } from "electron";

type HostRuntimeInfo = {
  surface: "native";
  app_version: string;
  engine_status: "starting" | "ready" | "restarting" | "stopped" | "crashed";
  data_dir: string;
  logs_dir: string;
  config_path: string;
  workspace_path: string;
  python: string;
  engine_transport?: "unix_socket";
};

type HostSocketEvent =
  | { id: string; type: "open" }
  | { data: string; id: string; type: "message" }
  | { id: string; message: string; type: "error" }
  | { code?: number; id: string; reason?: string; type: "close" };

contextBridge.exposeInMainWorld("openbotHost", {
  getRuntimeInfo: (): Promise<HostRuntimeInfo> =>
    ipcRenderer.invoke("openbot:get-runtime-info"),
  restartEngine: (): Promise<void> => ipcRenderer.invoke("openbot:restart-engine"),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("openbot:pick-folder"),
  openLogs: (): Promise<void> => ipcRenderer.invoke("openbot:open-logs"),
  exportDiagnostics: (): Promise<string> =>
    ipcRenderer.invoke("openbot:export-diagnostics"),
  checkForUpdates: (): Promise<{ supported: boolean; message?: string }> =>
    ipcRenderer.invoke("openbot:check-for-updates"),
  openSocket: (url: string): Promise<string> =>
    ipcRenderer.invoke("openbot:ws-connect", url),
  sendSocket: (id: string, data: string): Promise<void> =>
    ipcRenderer.invoke("openbot:ws-send", id, data),
  closeSocket: (id: string): Promise<void> =>
    ipcRenderer.invoke("openbot:ws-close", id),
  onSocketEvent: (
    listener: (event: HostSocketEvent) => void,
  ): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: HostSocketEvent) => {
      listener(payload);
    };
    ipcRenderer.on("openbot:ws-event", handler);
    return () => ipcRenderer.removeListener("openbot:ws-event", handler);
  },
  onRuntimeStatus: (
    listener: (status: HostRuntimeInfo["engine_status"]) => void,
  ): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: HostRuntimeInfo["engine_status"]) => {
      listener(status);
    };
    ipcRenderer.on("openbot:runtime-status", handler);
    return () => ipcRenderer.removeListener("openbot:runtime-status", handler);
  },
});
