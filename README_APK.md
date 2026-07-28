# AI Inventory Manager - Android APK Guide

This project includes a complete native Android WebView application located in the `android_app/` directory.

---

## 🚀 How to Download Your APK (Automated via GitHub)

Because GitHub Actions build automation has been added to your repository:

1. Push your latest code to GitHub (or open your repository at [madhav2627/AI_INVENTORY_MANAGER](https://github.com/madhav2627/AI_INVENTORY_MANAGER.git)).
2. Go to the **Actions** tab on GitHub.
3. Click on the latest workflow run named **"Build Android APK"**.
4. Scroll down to the **Artifacts** section at the bottom of the page.
5. Click **`AI_Inventory_Manager_v1.0.apk`** to download your ready-to-install Android APK file!
6. Transfer the `.apk` file to your Android phone and tap **Install**.

---

## 🛠️ How to Open or Build Locally in Android Studio

If you want to customize the app locally or build manually:

1. Download & open **[Android Studio](https://developer.android.com/studio)**.
2. Select **Open an Existing Project** and choose the `android_app` folder.
3. To change the target URL loaded by the app, edit `android_app/app/src/main/res/values/strings.xml`:
   ```xml
   <string name="default_web_url">https://your-app-name.onrender.com</string>
   ```
4. Click **Build** $\rightarrow$ **Build Bundle(s) / APK(s)** $\rightarrow$ **Build APK(s)**.
