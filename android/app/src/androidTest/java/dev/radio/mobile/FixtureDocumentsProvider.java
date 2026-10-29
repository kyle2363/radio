package dev.radio.mobile;

import android.content.Intent;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.ParcelFileDescriptor;
import android.provider.DocumentsContract;
import android.provider.DocumentsContract.Document;
import android.provider.DocumentsContract.Root;
import android.provider.DocumentsProvider;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Test-only read-only provider. Fixture bytes stay in its private .test-runtime cache. */
public class FixtureDocumentsProvider extends DocumentsProvider {
    public static final String AUTHORITY = "dev.radio.mobile.test.documents";
    public static Uri tree() { return tree("root"); }
    public static Uri tree(String id) { return DocumentsContract.buildTreeDocumentUri(AUTHORITY, id); }
    private static class Entry {
        final String id, parent, name, mime; final long modified;
        Entry(String id, String parent, String name, String mime, long modified) {
            this.id = id; this.parent = parent; this.name = name; this.mime = mime; this.modified = modified;
        }
    }
    private final Map<String, Entry> entries = new LinkedHashMap<>();
    private String failFolder, slowFolder;
    private int writeAttempts;
    @Override public boolean onCreate() { return true; }
    @Override public Bundle call(String method, String arg, Bundle extras) {
        // Android can call back into this provider while checking grants: don't hold its lock.
        if (method.equals("grant")) {
            getContext().grantUriPermission("dev.radio.mobile", tree(arg == null ? "root" : arg), Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);
            return new Bundle();
        }
        if (method.equals("revoke")) {
            getContext().revokeUriPermission(tree(arg == null ? "root" : arg), Intent.FLAG_GRANT_READ_URI_PERMISSION);
            return new Bundle();
        }
        synchronized (this) {
            switch (method) {
                case "reset":
                    entries.clear(); failFolder = null; slowFolder = null; writeAttempts = 0;
                    add("root", null, "Music", Document.MIME_TYPE_DIR, 1000);
                    String[] folders = {"October 2026", "September 2026", "August 2026", "July 2026", "June 2026", "May 2026", "April 2026", "March 2026"};
                    for (int i = 0; i < folders.length; i++) add("d" + i, "root", folders[i], Document.MIME_TYPE_DIR, 8000 - i);
                    add("nested", "d0", "Nested", Document.MIME_TYPE_DIR, 1000);
                    add("a", "d0", "Duplicate.mp3", "audio/mpeg", 9000);
                    add("b", "nested", "Duplicate.MP3", "application/octet-stream", 2000);
                    add("c", "root", "Root song.mp3", "audio/mpeg", 5000);
                    add("ignored", "root", "Notes.txt", "text/plain", 1000);
                    break;
                case "remove": entries.remove(arg); break;
                case "add": add("new", "d1", "Newest.mp3", "audio/mpeg", 15000); break;
                case "fail": failFolder = arg; break;
                case "slow": slowFolder = arg; break;
                case "audio":
                    entries.clear(); failFolder = null; slowFolder = null;
                    add("root", null, "Music", Document.MIME_TYPE_DIR, 1000);
                    add("long1", "root", "First.mp3", "audio/mpeg", 8000);
                    add("long2", "root", "Second.mp3", "audio/mpeg", 7000);
                    add("short1", "root", "Short.mp3", "audio/mpeg", 6000);
                    add("broken", "root", "Broken.mp3", "audio/mpeg", 5000);
                    break;
                case "background":
                    entries.clear(); failFolder = null; slowFolder = null;
                    add("root", null, "Music", Document.MIME_TYPE_DIR, 1000);
                    add("background1", "root", "Background one.mp3", "audio/mpeg", 8000);
                    add("background2", "root", "Background two.mp3", "audio/mpeg", 7000);
                    add("background3", "root", "Background three.mp3", "audio/mpeg", 6000);
                    break;
            }
            Bundle result = new Bundle(); result.putInt("writes", writeAttempts); return result;
        }
    }
    private void add(String id, String parent, String name, String mime, long modified) { entries.put(id, new Entry(id, parent, name, mime, modified)); }
    @Override public Cursor queryRoots(String[] projection) {
        String[] columns = projection == null ? new String[]{Root.COLUMN_ROOT_ID, Root.COLUMN_DOCUMENT_ID, Root.COLUMN_TITLE, Root.COLUMN_FLAGS} : projection;
        MatrixCursor cursor = new MatrixCursor(columns); Object[] row = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            switch(columns[i]) {
                case Root.COLUMN_ROOT_ID: row[i] = "fixture"; break;
                case Root.COLUMN_DOCUMENT_ID: row[i] = "root"; break;
                case Root.COLUMN_TITLE: row[i] = "Radio test library"; break;
                case Root.COLUMN_FLAGS: row[i] = Root.FLAG_LOCAL_ONLY | Root.FLAG_SUPPORTS_IS_CHILD; break;
            }
        }
        cursor.addRow(row); return cursor;
    }
    @Override public synchronized Cursor queryDocument(String id, String[] projection) throws FileNotFoundException {
        Entry entry = entries.get(id); if (entry == null) throw new FileNotFoundException(id);
        return result(projection, Collections.singletonList(entry));
    }
    @Override public synchronized Cursor queryChildDocuments(String parent, String[] projection, String sort) {
        if (parent.equals(failFolder)) throw new IllegalStateException("Synthetic query failure");
        if (parent.equals(slowFolder)) try { Thread.sleep(500); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        List<Entry> children = new ArrayList<>(); for (Entry e : entries.values()) if (parent.equals(e.parent)) children.add(e);
        return result(projection, children);
    }
    private Cursor result(String[] projection, List<Entry> rows) {
        String[] columns = projection == null ? new String[]{Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME, Document.COLUMN_MIME_TYPE, Document.COLUMN_LAST_MODIFIED, Document.COLUMN_SIZE, Document.COLUMN_FLAGS} : projection;
        MatrixCursor cursor = new MatrixCursor(columns);
        for (Entry e : rows) {
            Object[] row = new Object[columns.length];
            for (int i = 0; i < columns.length; i++) switch (columns[i]) {
                case Document.COLUMN_DOCUMENT_ID: row[i] = e.id; break;
                case Document.COLUMN_DISPLAY_NAME: row[i] = e.name; break;
                case Document.COLUMN_MIME_TYPE: row[i] = e.mime; break;
                case Document.COLUMN_LAST_MODIFIED: row[i] = e.modified; break;
                case Document.COLUMN_SIZE: row[i] = 32L; break;
                case Document.COLUMN_FLAGS: row[i] = 0; break;
            }
            cursor.addRow(row);
        }
        return cursor;
    }
    @Override public synchronized ParcelFileDescriptor openDocument(String id, String mode, CancellationSignal signal) throws FileNotFoundException {
        if (!mode.equals("r")) { writeAttempts++; throw new SecurityException("Read only"); }
        if (!entries.containsKey(id)) throw new FileNotFoundException(id);
        File directory = new File(getContext().getCacheDir(), ".test-runtime/documents"); directory.mkdirs();
        File file = new File(directory, id);
        if (!file.exists()) try (FileOutputStream out = new FileOutputStream(file)) {
            if (id.startsWith("long") || id.startsWith("short") || id.startsWith("background")) {
                String asset = id.startsWith("background") ? "silence-background.mp3" : id.startsWith("long") ? "silence-long.mp3" : "silence-short.mp3";
                try (InputStream in = getContext().getAssets().open(asset)) {
                    byte[] buffer = new byte[8192]; int count; while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
                }
            } else out.write("Synthetic invalid MP3; retain filename".getBytes(StandardCharsets.UTF_8));
        }
        catch (IOException e) { throw new FileNotFoundException(e.toString()); }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }
    @Override public synchronized boolean isChildDocument(String parent, String id) {
        while (id != null) { if (id.equals(parent)) return true; Entry entry = entries.get(id); id = entry == null ? null : entry.parent; }
        return false;
    }
}
