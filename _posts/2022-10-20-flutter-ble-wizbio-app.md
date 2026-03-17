---
layout: post
title: Flutter BLE 디바이스 통신 앱 개발 - C# 알고리즘 Dart 포팅
subtitle: 위즈바이오 의료기기 데이터 측정 앱 - BLE 연결부터 알고리즘 이식까지
author: HyeongJin
date: 2022-10-20 10:00:00 +0900
categories: React
tags: [Flutter, Dart, BLE, mobile]
sidebar: []
published: true
---

서울애널리티카에서 위즈바이오 Flutter 앱을 맡았다. 의료기기 관련 BLE 디바이스와 통신해서 데이터를 측정하고 표시하는 앱이었다.

기존에 C#으로 작성된 알고리즘이 있었고, 이걸 Dart로 포팅해서 Flutter 앱에 넣는 게 핵심 작업이었다. App Store / Play Store 출시까지 담당했다.

## Flutter BLE 통신

BLE 통신에는 `flutter_blue_plus` 패키지를 썼다.

```dart
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

class BleService {
  BluetoothDevice? _device;
  BluetoothCharacteristic? _characteristic;

  // 디바이스 스캔 및 연결
  Future<void> connect(String deviceName) async {
    await FlutterBluePlus.startScan(timeout: const Duration(seconds: 10));

    FlutterBluePlus.scanResults.listen((results) async {
      for (ScanResult r in results) {
        if (r.device.localName == deviceName) {
          await FlutterBluePlus.stopScan();
          await r.device.connect();
          _device = r.device;
          await _discoverServices();
          break;
        }
      }
    });
  }

  // 서비스/특성 탐색
  Future<void> _discoverServices() async {
    final services = await _device!.discoverServices();
    for (BluetoothService service in services) {
      for (BluetoothCharacteristic char in service.characteristics) {
        if (char.properties.notify) {
          _characteristic = char;
          await char.setNotifyValue(true);
        }
      }
    }
  }

  // 데이터 수신
  Stream<List<int>> get dataStream =>
      _characteristic!.lastValueStream;
}
```

디바이스에서 데이터가 들어오면 `notify`로 실시간으로 받아서 처리하는 방식이다.

## C# → Dart 알고리즘 포팅

가장 어려운 부분이었다. C# 코드가 수백 줄인데 Dart로 그대로 옮겨야 했다.

**타입 차이**

C#의 `byte[]`가 Dart에서는 `Uint8List`다. 비트 연산이 많아서 타입 변환을 정확히 맞춰야 했다.

```csharp
// C# 원본
private int ParseRawValue(byte[] data) {
    int value = (data[1] << 8) | data[0];
    return value;
}
```

```dart
// Dart 포팅
int parseRawValue(Uint8List data) {
  int value = (data[1] << 8) | data[0];
  return value;
}
```

비트 연산은 거의 동일하게 옮길 수 있었다.

**부동소수점 처리**

C#의 `float`는 32비트, Dart의 `double`은 64비트다. 디바이스에서 float 바이트를 받아서 파싱할 때 정밀도 차이가 있었다.

```dart
// 4바이트 big-endian float 파싱
double parseFloat(Uint8List bytes) {
  final byteData = ByteData.sublistView(bytes);
  return byteData.getFloat32(0, Endian.little);
}
```

`ByteData`의 `getFloat32`로 C#과 동일한 float 파싱이 됐다.

## Django 백엔드 연동

측정 데이터를 백엔드에 저장하고, 이력을 보여주는 기능도 추가했다. 기존 Django 데이터 플랫폼에 API 엔드포인트를 추가하고 앱에서 연동했다.

```dart
Future<void> saveMeasurement(MeasurementData data) async {
  final response = await http.post(
    Uri.parse('$baseUrl/api/measurements/'),
    headers: {'Authorization': 'Bearer $token'},
    body: jsonEncode(data.toJson()),
  );
  if (response.statusCode != 201) {
    throw Exception('저장 실패: ${response.body}');
  }
}
```

## App Store / Play Store 출시

iOS는 Xcode에서 Archive 후 App Store Connect로 업로드, Android는 `flutter build apk --release`로 빌드 후 Play Console에 올렸다.

BLE 기능 때문에 iOS에서 `Info.plist`에 권한 설명을 추가해야 심사를 통과했다.

```xml
<!-- Info.plist -->
<key>NSBluetoothAlwaysUsageDescription</key>
<string>디바이스 연결을 위해 블루투스 접근이 필요합니다.</string>
```

Android도 BLE 권한이 버전마다 달라서 대응이 필요했다. Android 12부터 `BLUETOOTH_SCAN`, `BLUETOOTH_CONNECT` 권한이 별도로 생겼다.

C# 알고리즘을 Dart로 포팅하면서 언어마다 숫자 타입과 메모리 표현 방식이 다르다는 걸 직접 느꼈다. 특히 BLE처럼 바이트 단위로 데이터를 다루는 경우에는 타입 처리를 정확히 이해해야 버그를 안 낸다.
