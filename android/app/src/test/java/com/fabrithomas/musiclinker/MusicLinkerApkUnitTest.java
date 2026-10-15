package com.fabrithomas.musiclinker;

import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.junit.Test;

public class MusicLinkerApkUnitTest {

    @Test
    public void bundledWebAssets_existAndContainDownloaders() throws Exception {
        File assetsDir = new File("src/main/assets/public");
        assertTrue("Public web assets directory must exist", assetsDir.exists() && assetsDir.isDirectory());

        File indexHtml = new File(assetsDir, "index.html");
        File appJs = new File(assetsDir, "js/app.js");
        File stylesCss = new File(assetsDir, "css/styles.css");

        assertTrue("index.html must be bundled in APK assets", indexHtml.exists());
        assertTrue("js/app.js must be bundled in APK assets", appJs.exists());
        assertTrue("css/styles.css must be bundled in APK assets", stylesCss.exists());

        String htmlContent = new String(Files.readAllBytes(indexHtml.toPath()), StandardCharsets.UTF_8);
        assertTrue("index.html must include Single Track Downloader input", htmlContent.contains("dl-single-query"));
        assertTrue("index.html must include Full Album & Playlist Downloader", htmlContent.contains("dl-album-title"));

        String jsContent = new String(Files.readAllBytes(appJs.toPath()), StandardCharsets.UTF_8);
        assertTrue("app.js must implement previewSingleTrackOrLink", jsContent.contains("previewSingleTrackOrLink"));
        assertTrue("app.js must implement startDirectTrackDownload", jsContent.contains("startDirectTrackDownload"));
    }
}
