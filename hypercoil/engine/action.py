# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Triggered actions
~~~~~~~~~~~~~~~~~
Actions for sentry objects to perform in response to a detected trigger.
"""
import torch
from .sentry import SentryAction


class PropagateMultiplierFromTransform(SentryAction):
    def __init__(self, transform):
        super().__init__(trigger=['EPOCH'])
        self.transform = transform


class PropagateMultiplierFromEpochTransform(
    PropagateMultiplierFromTransform
):
    def propagate(self, sentry, received):
        self.message.update(('NU', self.transform(received['EPOCH'])))
        for s in sentry.listeners:
            s._listen(self.message)


class PropagateMultiplierFromRecursiveTransform(
    PropagateMultiplierFromTransform
):
    def propagate(self, sentry, received):
        for s in sentry.listeners:
            self.message.update(('NU', self.transform(s.nu)))
            s._listen(self.message)


class UpdateMultiplier(SentryAction):
    def __init__(self):
        super().__init__(trigger=['NU'])

    def propagate(self, sentry, received):
        sentry.nu = received['NU']


class ResetMultiplier(SentryAction):
    def __init__(self):
        super().__init__(trigger=['NU_BASE'])

    def propagate(self, sentry, received):
        for s in sentry.listeners:
            self.message.update(('NU', received['NU_BASE']))
            s._listen(self.message)


class RecordLoss(SentryAction):
    def __init__(self):
        super().__init__(trigger=['LOSS', 'NAME', 'NU'])

    def propagate(self, sentry, received):
        name = received['NAME']
        sentry.epoch_buffer[name] += [received['LOSS']]
        sentry.epoch_buffer[f'{name}_norm'] += (
            [received['LOSS'] / received['NU']])


class ArchiveLoss(SentryAction):
    def __init__(self):
        super().__init__(trigger=['EPOCH'])

    def propagate(self, sentry, received):
        staging = {}
        for loss, record in sentry.epoch_buffer.items():
            if len(record) == 0:
                continue
            staging[loss] = sum(record) / len(record)
            sentry.epoch_buffer[loss] = []
        if len(staging) == 0:
            return
        for loss, archive in sentry.archive.items():
            new = staging.get(loss, float('nan'))
            sentry.archive[loss] += [new]


class ModuleReport(SentryAction):
    def __init__(self, report_interval, save_root=None,
                 save_format='.png', *args, **kwargs):
        super().__init__(trigger=['EPOCH'])
        self.report_interval = report_interval
        self.save_root = save_root
        self.save_format = save_format
        self.args = args
        self.kwargs = kwargs

    def propagate(self, sentry, received):
        if received['EPOCH'] % self.report_interval == 0:
            #TODO: we might need to revisit this save scheme for compatibility
            # with multi-output reporters
            epoch = received['EPOCH']
            if self.save_root is not None:
                save = (
                    f'{self.save_root}_epoch-{epoch}{self.save_format}'
                )
            else:
                save = None
            try:
                sentry(*self.args, save=save, **self.kwargs)
            except TypeError: #save repeated or invalid as argument
                sentry(*self.args, **self.kwargs)

    def _register_trigger(self, sentry):
        epochs_check = [isinstance(i, Epochs) for i in sentry.listening]
        if not any(epochs_check):
            sentry.actions.remove(self)
            raise ValueError(
                'Cannot register reporter action to a sentry that is not '
                'listening to epochs. Register the sentry to an epochs '
                'instance first.'
            )


##TODO: change this functionality when we've made every module a sentry
class ModuleSave(SentryAction):
    def __init__(self, save_interval, module, save_root,
                 save_format='.pt', *args, **kwargs):
        super().__init__(trigger=['EPOCH'])
        self.save_interval = save_interval
        self.module = module
        self.save_root = save_root
        self.save_format = save_format
        self.args = args
        self.kwargs = kwargs

    def propagate(self, sentry, received):
        if received['EPOCH'] % self.save_interval == 0:
            epoch = received['EPOCH']
            to_save = self.module.state_dict()
            save_path = (
                f'{self.save_root}_epoch-{epoch}{self.save_format}'
            )
            torch.save(to_save, save_path)


class WriteTSV(SentryAction):
    def __init__(self, save_interval, save_root, overwrite=True):
        super().__init__(trigger=['EPOCH'])
        self.save_interval = save_interval
        self.save_root = save_root
        self.overwrite = overwrite

    def propagate(self, sentry, received):
        if received['EPOCH'] % self.save_interval == 0:
            to_save = sentry.data
            if self.overwrite:
                save_path = f'{self.save_root}.tsv'
            else:
                epoch = received['EPOCH']
                save_path = (
                    f'{self.save_root}_epoch-{epoch}.tsv'
                )
            to_save.to_csv(save_path, index=False, sep='\t')


class Convey(SentryAction):
    def __init__(self, receive_line=None, transmit_line=None):
        self.receive_line = receive_line
        self.transmit_line = transmit_line
        super().__init__(trigger=['DATA'])

    def propagate(self, sentry, received):
        input = received['DATA'].get(self.receive_line)
        if input is not None:
            sentry(input, line=self.transmit_line)


class CountBatches(SentryAction):
    def __init__(self):
        super().__init__(trigger=['BATCH'])

    def propagate(self, sentry, received):
        count = received['BATCH']
        sentry.batched += count


class BatchRelease(SentryAction):
    def __init__(self, batch_size):
        super().__init__(trigger=['BATCH'])
        self.batch_size = batch_size

    def propagate(self, sentry, received):
        if sentry.batched >= self.batch_size:
            sentry.release()
            sentry.batched = 0


class VerboseReceive(SentryAction):
    def __init__(self):
        super().__init__(trigger=None)

    def propagate(self, sentry, received):
        print(f'\n[Sentry {sentry} receiving transmission]')
        print(received)
