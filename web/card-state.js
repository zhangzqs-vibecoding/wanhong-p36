const storeName = "sectors";
const sectorSize = 512;
const recordSize = 4 + sectorSize;

async function imageKey(file) {
  const prefix = await file.slice(0, 256 * 1024).arrayBuffer();
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", prefix));
  const hex = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
  return JSON.stringify([file.name, file.size, file.lastModified, hex]);
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("p36-sd-state", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(storeName, { keyPath: ["image", "sector"] });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function lockImage(image) {
  return new Promise((resolve, reject) => {
    const held = navigator.locks.request(`p36-sd:${image}`, { ifAvailable: true }, lock => {
      if (!lock) throw new Error("该 SD 卡镜像已在另一个页面打开。");
      return new Promise(release => resolve(() => {
        release();
        return held;
      }));
    });
    held.catch(reject);
  });
}

export async function openCardState(file) {
  if (!globalThis.indexedDB || !navigator.locks || !crypto.subtle) return undefined;
  const image = await imageKey(file);
  const unlock = await lockImage(image);
  try {
    const database = await openDatabase();
    return {
      read() {
        return new Promise((resolve, reject) => {
          const store = database.transaction(storeName).objectStore(storeName);
          const range = IDBKeyRange.bound([image, 0], [image, 0xffffffff]);
          const request = store.getAll(range);
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
      },
      write(records) {
        const view = new DataView(records.buffer, records.byteOffset, records.byteLength);
        return new Promise((resolve, reject) => {
          const transaction = database.transaction(storeName, "readwrite", { durability: "strict" });
          const store = transaction.objectStore(storeName);
          transaction.oncomplete = resolve;
          transaction.onabort = () => reject(transaction.error ?? new Error("保存 SD 卡数据失败。"));
          for (let offset = 0; offset < records.length; offset += recordSize) {
            const sector = view.getUint32(offset, true);
            const bytes = records.slice(offset + 4, offset + recordSize);
            store.put({ image, sector, bytes });
          }
        });
      },
      close() {
        database.close();
        return unlock();
      },
    };
  } catch (error) {
    await unlock();
    throw error;
  }
}
